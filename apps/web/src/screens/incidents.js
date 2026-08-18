/**
 * Incidents: record that something happened, and nothing more than that.
 *
 * This screen is small and its restraint is the point. Four of its choices are deliberate rather
 * than incidental:
 *
 *   - **Opening an incident decides nothing.** `FR-INC-002` and `FR-INC-003` separate the intake
 *     record from the authority to say whose fault it was and what the customer gets. The server
 *     honours that separation literally: `fault_decided` and `remedy_decided` come back `false` and
 *     there is no route in this API that ever sets either. So both are rendered as
 *     "chưa quyết định" everywhere they appear, never as a blank or an omitted row, and the missing
 *     remedy workflow is linked rather than implied.
 *   - **The hashes use a bare `sha256:` prefix.** Every other hash this API accepts is
 *     `JCS-SHA256-V1:` — approval envelopes, quote snapshots, rendered messages. Incident hashes are
 *     the exception (`0014_customer_incidents.sql:9,14`), and pasting the wrong kind produces a 422
 *     whose text an operator cannot act on. Both fields are therefore checked here before the round
 *     trip, marked `aria-invalid`, and their hint names the prefix explicitly.
 *   - **There is no category picker, because there is no category field.** The HTTP model has three
 *     members and none of them is `category`; the service hardcodes `SERVICE_QUALITY` and
 *     `actor_type=STAFF` (`operations.py:645,657`). Offering a choice the request cannot carry would
 *     be a lie. Stored data does contain `AUTOMATED_MESSAGE_ERROR` — the agent path writes it — which
 *     is why the list below still renders whatever category comes back.
 *   - **`order_id` is required here although the domain permits null.** The domain accepts an
 *     incident bound to a message instead of an order; `IncidentOpenRequest` types `order_id` as a
 *     plain `UUID`, so this HTTP surface cannot express that case at all. The form says so instead
 *     of pretending the field is optional.
 *
 * @module screens/incidents
 */

import { Submission, request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { UUID, dateTime, shortId } from "../core/format.js";
import { WARNING, enumLabel } from "../core/i18n.js";
import { can } from "../core/rbac.js";
import { principal, storeId } from "../core/session.js";
import {
  badge,
  boundInput,
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

const LIST_LIMIT = 100;

/**
 * `IncidentOpenRequest.contact_scope_hash` and `.evidence_summary_hash`.
 *
 * Matched here only to catch the wrong prefix before a round trip. The server checks the same
 * pattern, and the database checks it a third time; this copy exists to make the refusal legible,
 * not to be the authority.
 */
const INCIDENT_HASH = /^sha256:[0-9a-f]{64}$/;

/**
 * The two domain refusals this route can answer with, keyed by the server's exact string.
 *
 * The string itself is always shown verbatim by `errorNotice` — it is what an engineer greps for.
 * These notes are rendered beside it, never instead of it, because "incident binding is invalid"
 * tells an operator nothing about which of the two hashes to look at.
 */
const REFUSAL_NOTE = {
  "incident order binding is unavailable":
    "Mã đơn không tồn tại trong cửa hàng đang chọn. Kiểm tra lại mã đơn, hoặc kiểm tra bạn đang " +
    "đứng đúng cửa hàng. Không có sự cố nào được ghi.",
  "incident binding is invalid":
    "Ràng buộc của sự cố không qua được kiểm tra miền — thường là một trong hai mã băm sai định " +
    "dạng. Không có sự cố nào được ghi.",
};

/**
 * The cross-screen hand-off staged by order detail's "Mở sự cố cho đơn này" action.
 *
 * Module state, in memory only — invariant 3 of the UX refactor spec permits nothing else,
 * and an order id is not a secret worth persisting. The render below reads it once and clears
 * it, so a later visit to this form starts empty rather than resurrecting a stale order. It is
 * the same idea as the staff screen's create-to-assign carry-over, but between routes instead
 * of between panels on one screen.
 */
let orderPrefill = "";

/**
 * Stage an order UUID for this screen's next render.
 *
 * @param {string} orderId
 */
export function setIncidentOrderPrefill(orderId) {
  orderPrefill = String(orderId || "");
}

/**
 * How a fault or remedy flag is shown.
 *
 * Never blank, never an empty state. `false` here does not mean "no fault"; it means nobody has
 * decided yet, and the two read very differently to someone standing in front of a customer.
 *
 * @param {boolean|null|undefined} decided
 * @returns {string}
 */
function decisionLabel(decided) {
  return decided === true ? "đã quyết định" : "chưa quyết định";
}

/**
 * The standing statement that this screen records and does not adjudicate.
 *
 * Rendered twice on purpose: once above the form, where it sets the expectation, and once inside
 * the success result, where the operator is looking at two `false` flags and needs to know they are
 * the correct outcome rather than a failure to save something.
 *
 * @returns {HTMLElement}
 */
function recordOnlyNotice() {
  return h(
    "div",
    { class: "notice", dataState: "info" },
    h("p", { class: "notice__title" }, "Ghi nhận sự cố — không phán quyết lỗi, không quyết bồi hoàn"),
    h(
      "p",
      null,
      "Mở sự cố chỉ ghi lại rằng có chuyện xảy ra. Máy chủ luôn trả về fault_decided = false và " +
        "remedy_decided = false, và trong API này không có route nào đặt hai giá trị đó. Ai chịu " +
        "lỗi và khách được bù gì là quyết định của con người, ở nơi khác.",
    ),
    h(
      "p",
      { class: "hint" },
      "Luồng duyệt bồi hoàn là một lệnh cần phê duyệt và hiện chưa tồn tại: ",
      h("a", { href: "#/gaps" }, "xem danh sách khoảng trống"),
      ".",
    ),
  );
}

/**
 * What this form always writes, whatever the operator types.
 *
 * @returns {HTMLElement}
 */
function hardcodedFieldsNotice() {
  return h(
    "div",
    { class: "notice", dataState: "warn" },
    h("p", { class: "notice__title" }, "Mọi sự cố mở ở đây đều được ghi giống nhau"),
    h(
      "p",
      null,
      "Máy chủ tự đặt category = ",
      h("span", { class: "mono" }, "SERVICE_QUALITY"),
      " và actor_type = ",
      h("span", { class: "mono" }, "STAFF"),
      ". Yêu cầu HTTP không có trường loại sự cố, nên màn hình này không cho chọn. Loại ",
      h("span", { class: "mono" }, "AUTOMATED_MESSAGE_ERROR"),
      " chỉ do đường agent ghi, và vẫn hiện trong danh sách bên dưới.",
    ),
  );
}

/**
 * The extra note for a refusal whose text this screen recognises.
 *
 * @param {import("../core/errors.js").ApiError|Error} error
 * @returns {HTMLElement|null}
 */
function refusalNote(error) {
  const api = /** @type {import("../core/errors.js").ApiError} */ (error);
  const note = REFUSAL_NOTE[api?.detail] || REFUSAL_NOTE[error?.message];
  if (!note) return null;
  return h(
    "div",
    { class: "notice", dataState: "info" },
    h("p", { class: "notice__title" }, "Máy chủ từ chối vì ràng buộc, không phải vì lỗi hệ thống"),
    h("p", null, note),
  );
}

/**
 * Render a created incident.
 *
 * @param {any} result
 * @returns {HTMLElement}
 */
function incidentResult(result) {
  return h(
    "div",
    { class: "card stack" },
    h(
      "div",
      { class: "spread" },
      h("h3", null, "Đã ghi sự cố"),
      h(
        "div",
        { class: "row" },
        result.status === "OPEN" ? badge(WARNING.INCIDENT) : null,
      ),
    ),
    result.replayed
      ? h(
          "div",
          { class: "notice", dataState: "info" },
          "Kết quả được phát lại: cùng khoá thao tác và cùng nội dung đã gửi trước đó. Không có " +
            "sự cố mới nào được tạo.",
        )
      : null,
    facts([
      [
        "Mã sự cố",
        h("span", { title: result.incident_id || null }, shortId(result.incident_id)),
        { mono: true, span: true },
      ],
      ["Trạng thái", enumLabel(result.status), { mono: true }],
      ["Lỗi thuộc về ai", decisionLabel(result.fault_decided)],
      ["Bồi hoàn cho khách", decisionLabel(result.remedy_decided)],
    ]),
    recordOnlyNotice(),
  );
}

/**
 * One incident on the list.
 *
 * @param {any} item
 * @returns {HTMLElement}
 */
function incidentCard(item) {
  return h(
    "article",
    { class: "card" },
    h(
      "div",
      { class: "spread" },
      h("strong", { class: "mono", title: item.incident_id }, shortId(item.incident_id)),
      // The mandated INCIDENT warning, and only while the record is still open. A closed
      // incident is history; a red badge on it would train staff to ignore the red badge.
      item.status === "OPEN" ? badge(WARNING.INCIDENT) : null,
    ),
    facts([
      ["Loại", enumLabel(item.category), { mono: true, span: true }],
      ["Trạng thái", enumLabel(item.status), { mono: true }],
      [
        "Đơn liên quan",
        h("span", { title: item.order_id || null }, shortId(item.order_id)),
        { mono: true },
      ],
      ["Lỗi thuộc về ai", decisionLabel(item.fault_decided)],
      ["Bồi hoàn cho khách", decisionLabel(item.remedy_decided)],
      ["Mở lúc", dateTime(item.opened_at)],
    ]),
  );
}

/**
 * @returns {HTMLElement}
 */
export function render_() {
  const store = storeId();
  const submission = new Submission("incident-open");
  const writeVerdict = can(principal(), "INCIDENTS_WRITE");

  /** @type {{orderId: string, contactScopeHash: string, evidenceSummaryHash: string}} */
  const draft = { orderId: "", contactScopeHash: "", evidenceSummaryHash: "" };

  // A staged hand-off is consumed exactly once, here, before the inputs are built — the
  // order field opens holding the carried id.
  const staged = orderPrefill;
  orderPrefill = "";
  if (staged) draft.orderId = staged;

  /**
   * The exact payload of the last successful commit.
   *
   * The idempotency key is reset after a commit, as it must be — the next submission is a new
   * intent. But an operator who taps twice on a slow connection would then send the identical body
   * under a fresh key, and the server would happily record a second incident. So an unchanged
   * resubmission is refused here, with an instruction, rather than silently duplicated. Nothing is
   * retried automatically; this only declines to send.
   *
   * @type {string}
   */
  let lastCommitted = "";

  const resultHost = h("div", { class: "stack" });
  const result = resultLine();

  /**
   * The recorded incidents. The filter narrows the rows already fetched, and nothing else: a
   * case-insensitive substring test over the raw `incident_id`, `order_id` and `status` strings —
   * the same values the row renders. It computes nothing and touches no money field; while it is
   * active both counts stay on screen so a shortened list never reads as lost data.
   */
  const list = listView({
    limit: LIST_LIMIT,
    fetch: () =>
      request(`/internal/v1/stores/${encodeURIComponent(store)}/incidents?limit=${LIST_LIMIT}`),
    renderItem: incidentCard,
    emptyText: "Chưa có sự cố nào trong cửa hàng này.",
    skeletonRows: 2,
    filter: {
      placeholder: "Lọc theo mã sự cố, mã đơn, trạng thái…",
      noun: "sự cố",
      matches: (item, needle) =>
        [item.incident_id, item.order_id, item.status].some(
          (value) => typeof value === "string" && value.toLowerCase().includes(needle),
        ),
    },
  });

  const orderInput = boundInput({
    target: draft,
    key: "orderId",
    pattern: UUID,
    placeholder: "00000000-0000-0000-0000-000000000000",
    submission,
  });

  const contactInput = boundInput({
    target: draft,
    key: "contactScopeHash",
    pattern: INCIDENT_HASH,
    placeholder: "sha256:0000000000000000000000000000000000000000000000000000000000000000",
    submission,
    format: "hash",
  });

  const evidenceInput = boundInput({
    target: draft,
    key: "evidenceSummaryHash",
    pattern: INCIDENT_HASH,
    placeholder: "sha256:0000000000000000000000000000000000000000000000000000000000000000",
    submission,
    format: "hash",
  });

  /**
   * @returns {string} empty when the draft may be sent
   */
  function validate() {
    if (!draft.orderId) return "Chưa nhập mã đơn (UUID). Máy chủ bắt buộc phải có đơn.";
    if (!UUID.test(draft.orderId)) return "Mã đơn phải là UUID đủ 36 ký tự.";
    if (!draft.contactScopeHash) return "Chưa nhập mã băm phạm vi liên hệ (contact_scope_hash).";
    if (!INCIDENT_HASH.test(draft.contactScopeHash)) {
      return "Mã băm phạm vi liên hệ (contact_scope_hash) phải bắt đầu bằng sha256: và có đúng 64 ký tự hex thường.";
    }
    if (!draft.evidenceSummaryHash) return "Chưa nhập mã băm tóm tắt bằng chứng (evidence_summary_hash).";
    if (!INCIDENT_HASH.test(draft.evidenceSummaryHash)) {
      return "Mã băm tóm tắt bằng chứng (evidence_summary_hash) phải bắt đầu bằng sha256: và có đúng 64 ký tự hex thường.";
    }
    return "";
  }

  /**
   * @param {SubmitEvent} event
   */
  async function submit(event) {
    event.preventDefault();
    const problem = validate();
    if (problem) {
      setResult(result, "danger", problem);
      return;
    }

    const payload = {
      order_id: draft.orderId,
      contact_scope_hash: draft.contactScopeHash,
      evidence_summary_hash: draft.evidenceSummaryHash,
    };
    const signature = JSON.stringify(payload);
    if (signature === lastCommitted) {
      setResult(
        result,
        "warn",
        "Nội dung này vừa được ghi thành công. Gửi lại y nguyên sẽ tạo thêm một sự cố thứ hai, " +
          "nên máy không gửi. Sửa dữ liệu nếu đây là sự cố khác, hoặc xem danh sách bên dưới.",
      );
      return;
    }

    setResult(result, "warn", "Đang ghi sự cố…");
    render(resultHost);

    try {
      const created = await request(`/internal/v1/stores/${encodeURIComponent(store)}/incidents`, {
        method: "POST",
        body: payload,
        idempotencyKey: submission.key(),
      });
      submission.reset();
      lastCommitted = signature;
      // Confirmed exactly once, in one place.
      setResult(
        result,
        "ok",
        `Đã ghi sự cố ${shortId(created.incident_id)}. Chưa có phán quyết lỗi và chưa có bồi hoàn.`,
      );
      render(resultHost, incidentResult(created));
      await list.reload();
    } catch (error) {
      // A refusal is the system working. A binding refusal, a denial and a REQUIRE_HUMAN are
      // outcomes with a cause the operator can act on; only the rest are presented as breakage.
      const note = refusalNote(error);
      const refused = Boolean(note) || error.kind === "DENIED" || error.kind === "REQUIRE_HUMAN";
      setResult(
        result,
        refused ? "warn" : "danger",
        error.kind === "DENIED"
          ? "Máy chủ từ chối thao tác này cho phiên hiện tại. Không có sự cố nào được ghi."
          : refused
            ? "Máy chủ từ chối ràng buộc của sự cố. Không có sự cố nào được ghi."
            : "Không ghi được sự cố.",
      );
      // A write is never retried by software, so no retry handler is offered here.
      const notice = errorNotice(error);
      render(resultHost, notice, note);
      revealError(notice);
    }
  }

  const submitButton = h(
    "button",
    { type: "submit", dataVariant: "primary", dataRequiresNetwork: "true" },
    "Ghi sự cố",
  );

  const form = h(
    "form",
    { class: "form", onSubmit: submit },
    recordOnlyNotice(),
    hardcodedFieldsNotice(),
    labelled({
      id: "incident-order",
      label: "Mã đơn (order_id)",
      hint:
        "Bắt buộc. Miền cho phép sự cố gắn với một tin nhắn thay vì một đơn, nhưng yêu cầu HTTP " +
        "này không có trường đó, nên ở đây phải có đơn. Đơn phải thuộc đúng cửa hàng đang chọn.",
      control: orderInput,
    }),
    labelled({
      id: "incident-contact-hash",
      label: "Mã băm phạm vi liên hệ",
      hint:
        "contact_scope_hash — chép nguyên văn, gồm tiền tố sha256: rồi 64 ký tự hex thường — KHÔNG phải JCS-SHA256-V1: như mã băm của " +
        "báo giá hay phiếu duyệt. Dán nhầm loại sẽ bị máy chủ trả 422 khó hiểu.",
      control: contactInput,
    }),
    labelled({
      id: "incident-evidence-hash",
      label: "Mã băm tóm tắt bằng chứng",
      hint:
        "evidence_summary_hash — chép nguyên văn, gồm tiền tố sha256: rồi 64 ký tự hex thường. Đây là mã băm của bản tóm tắt bằng chứng, " +
        "không phải bản thân bằng chứng — không dán nội dung khách gửi vào đây.",
      control: evidenceInput,
    }),
    h("div", { class: "action-bar" }, gated(submitButton, writeVerdict)),
    result,
  );

  void list.reload();

  return h(
    "section",
    { class: "screen" },
    h(
      "div",
      { class: "screen__header" },
      h("p", { class: "eyebrow" }, "Bản ghi sự cố · Không phán quyết"),
      h("h1", null, "Sự cố"),
      h(
        "p",
        { class: "screen__lede" },
        "Ghi lại rằng có chuyện xảy ra, kèm mã băm phạm vi liên hệ và mã băm tóm tắt bằng chứng. " +
          "Việc quy lỗi và bồi hoàn không diễn ra ở đây.",
      ),
    ),
    panel({
      eyebrow: "Lệnh",
      title: "Mở một sự cố",
      guardrail:
        "Sự cố mở ở đây luôn là SERVICE_QUALITY do nhân viên ghi, luôn ở trạng thái OPEN, và luôn " +
        "chưa quyết định lỗi lẫn bồi hoàn. Không có route nào trong API này thay đổi ba điều đó.",
      children: h("div", { class: "stack" }, form, resultHost),
    }),
    panel({
      eyebrow: "Đã ghi",
      title: "Sự cố của cửa hàng",
      count: list.count,
      children: h(
        "div",
        { class: "stack" },
        list.bar.node,
        list.filterStatus,
        list.truncation,
        list.host,
      ),
    }),
  );
}

export const screen = {
  path: "/incidents",
  title: "Sự cố",
  capability: "INCIDENTS_READ",
  needsStore: true,
  render: render_,
};
