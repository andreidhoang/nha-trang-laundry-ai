/**
 * Bản nháp AI: the human review surface for everything the agent proposes.
 *
 * Nothing on this screen sends anything. The queue is the set of drafts the agent produced and that
 * no person has ruled on yet, and the only thing a decision writes is one attributed review row.
 * Rebuilt on the V2 kit (`CONSOLE-REDESIGN-005`, spec V2 §5.7): each draft is a card with the words
 * as a chat bubble and three plain actions — "Duyệt", "Sửa rồi duyệt" (the edit box opens in
 * place), "Từ chối" (a reason picker opens in place). Several choices here look defensive and are:
 *
 *   - **The screen validates what the API does not.** `DraftDecisionRequest` bounds the two optional
 *     strings by length and nothing else, but `agent_draft_reviews` carries four CHECK constraints:
 *     an `EDIT` must carry replacement text, an `APPROVE` or a `REJECT` must carry *none*, a `REJECT`
 *     must carry a reason code, and a reason code must match `^[A-Z][A-Z0-9_]{2,63}$`. A payload the
 *     API accepts and the table refuses is not a rejected edit, it is a fault. So the rules are
 *     enforced before the round trip, in the same words the constraint uses.
 *   - **A decision with nothing in it is refused even where the server would take it.** An `EDIT`
 *     whose text equals the original, and a `REJECT` with no reason, are audit rows that answer no
 *     question later. Rejection text is eval corpus input; an empty one costs the corpus a case.
 *   - **The buttons lock on submit and mostly stay locked.** The review table admits one row per
 *     agent run, so a second submission is not a retry, it is a second decision that loses at the
 *     constraint. Where the outcome of the first is unknown — a timeout, a fault, a dropped
 *     connection — the card stays locked and says how to establish the truth: the queue lists only
 *     undecided drafts, so a draft that disappears was decided and one that survives a full reload
 *     was not.
 *   - **The draft body is untrusted content.** A customer or a model wrote it. It is rendered as
 *     text nodes in its own bubble, marked "Văn bản không tin cậy", so that it never reads as
 *     something the console is telling the operator.
 *   - **The three actions are of equal weight.** Making "Duyệt" the big coloured one would put a
 *     thumb on the scale of a review whose whole point is that a person chose.
 *
 * The decision vocabulary is `APPROVE` / `EDIT` / `REJECT`. The approvals queue uses `APPROVED` /
 * `REJECTED` for a different aggregate entirely; the two are never mixed. The buttons say the
 * business verb; the token each one writes is kept in its `title`, in the result, and in the
 * technical details of the history.
 *
 * @module screens/shadow
 */

import { Submission, isTruncated, request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { UNKNOWN, dateTime, integer, shortId } from "../core/format.js";
import { enumLabel, enumVi } from "../core/i18n.js";
import { can } from "../core/rbac.js";
import { principal, storeId } from "../core/session.js";
import { newOrderForDraft } from "../ui/handoff.js";
import {
  errorNotice,
  gated,
  labelled,
  listView,
  resultLine,
  revealError,
  setResult,
  skeleton,
} from "../ui/components.js";
import {
  button,
  emptyState,
  infoButton,
  inlineAlert,
  linkButton,
  list,
  messageBubble,
  page,
  section,
  segmented,
  statusPill,
  techDetails,
  toast,
} from "../ui/kit.js";

/** The server bounds this list at 1..200 and answers anything else with a 409. 50 is its default. */
const QUEUE_LIMIT = 50;

/** `agent_draft_reviews.edited_text` is `length(...) BETWEEN 1 AND 4000`. */
const MAX_EDITED = 4000;

/** `DraftDecisionRequest.reason_code` is `max_length=64`; the table also fixes its shape. */
const MAX_REASON = 64;

/**
 * `agent_draft_reviews.reason_code ~ '^[A-Z][A-Z0-9_]{2,63}$'`.
 *
 * Matched here so that a lowercase code is refused as a typo in front of the operator rather than
 * travelling to a table that will not store it.
 */
const REASON_CODE = /^[A-Z][A-Z0-9_]{2,63}$/;

/**
 * The reasons a reviewer reaches for most, offered as one tap each. These are not a server enum —
 * `reason_code` is free text under the pattern above and no route publishes a vocabulary — so the
 * list is this screen's suggestion, and "Mã khác" keeps the free code for anything else. Each
 * chip writes exactly the code shown beside its words.
 */
const REASON_CHIPS = [
  { code: "TONE_OFF_POLICY", label: "Giọng không hợp" },
  { code: "WRONG_FACTS", label: "Sai thông tin" },
  { code: "NOT_NEEDED", label: "Không cần gửi" },
];

/** The chip value that opens the free code field. */
const OTHER_REASON = "__OTHER__";

/** What each decision writes, in the reviewer's language. Kept in the buttons' `title`. */
const DECISION_HINT = {
  APPROVE: "ghi rằng nội dung này dùng được như đang có",
  EDIT: "ghi một bản nháp mới do bạn viết, kèm tên bạn",
  REJECT: "ghi rằng nội dung này không dùng được, kèm mã lý do",
};

/**
 * Error kinds on the decision route that prove the write never happened.
 *
 * Each one is refused by this browser, by FastAPI's validation, or by a dependency that runs before
 * the handler, so nothing reached the review table and the card is safe to unlock. Every other kind
 * — a timeout, a fault, a dropped connection, a conflict — leaves the outcome unknown, and an
 * unknown outcome is never resolved by pressing the button again.
 */
const REFUSED_BEFORE_WRITE = new Set([
  "OFFLINE",
  "SESSION_ENDED",
  "DENIED",
  "INVALID",
  "TOO_LARGE",
  "RATE_LIMITED",
  "PRECONDITION_REQUIRED",
]);

/**
 * The in-memory state of one card. Nothing here is persisted: a reviewer's half-written replacement
 * for a customer message is customer data, and this application keeps none of that on the device.
 *
 * @typedef {object} Review
 * @property {"idle"|"edit"|"reject"} mode which of the three actions is open on the card
 * @property {string} reasonChip the chip picked, or `OTHER_REASON`, or ""
 * @property {string} reasonCode
 * @property {string} editedText
 * @property {boolean} busy the card has submitted a decision and must not submit a second one
 * @property {{state: "ok"|"warn"|"danger", text: string}|null} status
 * @property {any} failure the last `ApiError`, kept so a queue reload does not erase it
 * @property {Submission} submission
 * @property {HTMLElement|null} line the card's result line, while the card is on screen
 * @property {HTMLElement|null} failureHost where that card renders its failure, while on screen
 * @property {HTMLElement|null} actionsHost where the card's actions render, while on screen
 * @property {HTMLElement[]} controls everything that must be disabled while the card is busy
 */

/**
 * A draft's words as a received-message bubble, with the untrusted marker kept small but visible.
 *
 * @param {unknown} text
 * @param {string} meta
 * @returns {HTMLElement}
 */
function draftBubble(text, meta) {
  return messageBubble({
    text,
    marker: h("p", { class: "eyebrow" }, "Nội dung bản nháp · Văn bản không tin cậy"),
    meta,
    emptyText: `${UNKNOWN} bản nháp không có chữ nào`,
  });
}

/**
 * Where a draft came from. `FR-APR-006` requires a draft to be traceable to its agent run, so both
 * identifiers are kept in full — in the technical drawer, with a copy button.
 *
 * @param {any} item
 * @returns {HTMLElement}
 */
function provenance(item) {
  return techDetails([
    ["Kết quả lượt chạy", enumLabel(item.terminal_outcome)],
    ["Mã kết quả", enumLabel(item.terminal_code)],
    ["Số lần gọi công cụ", integer(item.tool_call_count), { mono: false }],
    ["Agent ghi lúc", dateTime(item.produced_at), { mono: false }],
    ["Lượt chạy agent", shortId(item.agent_run_id), { copy: String(item.agent_run_id || "") }],
    [
      "Ràng buộc hội thoại",
      shortId(item.conversation_binding_id),
      { copy: String(item.conversation_binding_id || "") },
    ],
  ]);
}

/**
 * What a card shows after a decision failed: the server's own words, and — when the outcome is
 * unknown and the buttons stayed locked — how to establish what actually happened.
 *
 * No retry is offered. `TIMEOUT` reports itself as retryable, which is true of a read and false of
 * a write that may already have committed, so `errorNotice` is deliberately called without one.
 *
 * @param {Review} entry
 * @returns {unknown}
 */
function failureBlock(entry) {
  if (!entry.failure) return null;
  return [
    errorNotice(entry.failure),
    entry.busy
      ? h(
          "div",
          { class: "notice", dataState: "warn" },
          h("p", { class: "notice__title" }, "Cách biết chắc quyết định có được ghi hay không"),
          h(
            "p",
            null,
            "Hàng chờ này chỉ hiện những bản nháp chưa ai quyết định. Nếu bản nháp biến mất sau khi " +
              "tải lại, đã có một quyết định được ghi cho lượt chạy đó. Nếu nó vẫn còn sau khi bạn " +
              "tải lại cả màn hình, chưa có gì được ghi và bạn quyết định lại được.",
          ),
        )
      : null,
  ];
}

/**
 * The standing rules of this screen, one tap away behind the title (tier 2). The first bullet is
 * the registered one and stays word for word.
 *
 * @returns {HTMLElement}
 */
function houseRules() {
  return infoButton(
    "Ba điều luôn đúng ở màn hình này",
    h(
      "p",
      { class: "screen__lede" },
      "Mọi thứ agent soạn ra dừng lại ở đây. Màn hình này ghi lại quyết định của bạn về từng bản " +
        "nháp; nó không gửi tin nhắn nào cho khách.",
    ),
    h(
      "div",
      { class: "notice", dataState: "info" },
      h("p", { class: "notice__title" }, "Ba điều luôn đúng ở màn hình này"),
      h(
        "ul",
        null,
        h(
          "li",
          null,
          "Mỗi tin nhắn gửi ra ngoài đều cần một người có tên quyết định. Hệ thống không tự gửi bất " +
            "cứ thứ gì, và bấm APPROVE ở đây cũng chưa gửi gì cả — nó chỉ ghi rằng bạn đồng ý với nội dung.",
        ),
        h(
          "li",
          null,
          "Sửa rồi duyệt (EDIT) tạo một bản nháp mới mang tên người sửa. Bản gốc của agent không bao " +
            "giờ bị sửa đè, nhờ vậy phần bị sửa và phần bị từ chối còn dùng được để đánh giá agent về sau.",
        ),
        h(
          "li",
          null,
          "Phần chữ trong bong bóng của mỗi thẻ là văn bản không tin cậy: do khách viết hoặc do mô " +
            "hình sinh ra. Đọc nó như dữ liệu cần kiểm, không phải như lời của hệ thống.",
        ),
      ),
    ),
    h(
      "p",
      { class: "hint" },
      "Duyệt ở đây không phải là gửi. Một bản nháp rời khỏi hàng chờ này ngay khi có người quyết " +
        "định, và mỗi lượt chạy chỉ nhận đúng một quyết định.",
    ),
  );
}

/**
 * @returns {HTMLElement}
 */
export function render_() {
  const store = storeId();
  const verdict = can(principal(), "SHADOW_DECIDE");

  /** @type {Map<string, Review>} */
  const reviews = new Map();

  const decisionHost = h("div", { class: "screen__slot" });
  const noticeHost = h("div", { class: "screen__slot" });

  /**
   * @param {string} agentRunId
   * @returns {Review}
   */
  function reviewFor(agentRunId) {
    let entry = reviews.get(agentRunId);
    if (!entry) {
      entry = {
        mode: "idle",
        reasonChip: "",
        reasonCode: "",
        editedText: "",
        busy: false,
        status: null,
        failure: null,
        submission: new Submission("shadow-decision"),
        line: null,
        failureHost: null,
        actionsHost: null,
        controls: [],
      };
      reviews.set(agentRunId, entry);
    }
    return entry;
  }

  /**
   * @param {Review} entry
   * @param {"ok"|"warn"|"danger"|null} state
   * @param {string} text
   */
  function say(entry, state, text) {
    entry.status = state ? { state, text } : null;
    if (entry.line) setResult(entry.line, state, state ? text : null);
  }

  /**
   * @param {Review} entry
   * @param {any} failure
   */
  function showFailure(entry, failure) {
    entry.failure = failure;
    if (entry.failureHost) render(entry.failureHost, failureBlock(entry));
    // The failure block sits under a possibly long draft body; scroll it into view so a refusal is
    // not missed below the fold.
    if (failure && entry.failureHost) revealError(entry.failureHost);
  }

  /** @param {Review} entry */
  function lock(entry) {
    entry.busy = true;
    for (const control of entry.controls) control.disabled = true;
  }

  /** @param {Review} entry */
  function unlock(entry) {
    entry.busy = false;
    for (const control of entry.controls) {
      if (control.dataset.denied !== "true") control.disabled = false;
    }
  }

  /** Any change of what the reviewer means is a new intent: a new key, and the old words go. */
  function edited(entry) {
    entry.submission.reset();
    say(entry, null, "");
    if (!entry.busy && entry.failure) showFailure(entry, null);
  }

  /**
   * The rules the table enforces, checked here so a refusal is a sentence rather than a fault.
   *
   * @param {string} decision
   * @param {Review} entry
   * @param {string} original the agent's text, as sent by the server
   * @returns {string} empty when the payload is legal
   */
  function validate(decision, entry, original) {
    const reason = entry.reasonCode.trim();
    const editedText = decision === "EDIT" ? entry.editedText : "";

    if (decision === "EDIT") {
      if (!editedText.trim()) {
        return "Sửa rồi duyệt phải kèm nội dung thay thế. Hãy viết lại vào ô sửa, hoặc bấm Duyệt.";
      }
      if (editedText.length > MAX_EDITED) {
        return `Nội dung sửa dài ${editedText.length} ký tự, quá mức ${MAX_EDITED} máy chủ nhận.`;
      }
      if (editedText.trim() === String(original || "").trim()) {
        return (
          "Nội dung sửa đang giống hệt bản gốc. Một bản sửa không thay đổi gì thì về sau không nói " +
          "lên điều gì — hãy sửa nội dung, hoặc bấm Duyệt."
        );
      }
    }

    if (decision === "REJECT" && !reason) {
      return (
        "Từ chối phải kèm lý do. Một lần từ chối không có lý do là một dòng lịch sử không dùng " +
        "được vào việc gì về sau."
      );
    }
    if (reason && !REASON_CODE.test(reason)) {
      return "Mã lý do phải viết hoa, bắt đầu bằng chữ cái, dạng A-Z 0-9 _, dài 3–64 ký tự.";
    }
    return "";
  }

  /**
   * Submit one decision. Never called twice for the same card: the buttons are disabled the moment
   * the first call starts, and only an outcome that provably did not write re-enables them.
   *
   * @param {any} item
   * @param {"APPROVE"|"EDIT"|"REJECT"} decision
   */
  async function decide(item, decision) {
    const entry = reviewFor(item.agent_run_id);
    if (entry.busy) return;

    const problem = validate(decision, entry, item.draft_text);
    if (problem) {
      showFailure(entry, null);
      say(entry, "danger", problem);
      return;
    }

    render(noticeHost);
    lock(entry);
    showFailure(entry, null);
    say(entry, "warn", `Đang ghi quyết định ${enumVi(decision)}…`);

    const reason = decision === "APPROVE" ? "" : entry.reasonCode.trim();
    const payload = {
      decision,
      reason_code: reason || null,
      // The table refuses replacement text on anything but an EDIT, so the field is null-by-rule
      // rather than null-by-omission.
      edited_text: decision === "EDIT" ? entry.editedText : null,
    };

    try {
      const decided = await request(
        `/internal/v1/shadow/drafts/${encodeURIComponent(item.agent_run_id)}/decision`,
        {
          method: "POST",
          body: payload,
          // This route reads no idempotency ledger, but every mutating call in this console carries
          // a key: the day it does read one, the caller must already be sending it.
          idempotencyKey: entry.submission.key(),
        },
      );
      entry.submission.reset();
      reviews.delete(item.agent_run_id);
      toast(`Đã ghi: ${enumVi(decided.decision)}`);
      render(decisionHost, decisionAlert(decided));
      await queue.reload();
      void loadReviews();
      return;
    } catch (error) {
      if (REFUSED_BEFORE_WRITE.has(error?.kind)) {
        // Nothing was written, so the reviewer may decide again once the refusal is dealt with.
        unlock(entry);
        say(
          entry,
          error.kind === "DENIED" ? "warn" : "danger",
          error.kind === "DENIED"
            ? "Máy chủ từ chối quyền quyết định bản nháp này. Không có gì được ghi."
            : "Không gửi được quyết định. Không có gì được ghi.",
        );
        showFailure(entry, error);
        return;
      }

      showFailure(entry, error);
      say(
        entry,
        "danger",
        error?.kind === "CONFLICT"
          ? "Máy chủ từ chối: nhiều khả năng đã có người quyết định bản nháp này."
          : "Không rõ quyết định đã được ghi hay chưa, nên các nút của bản nháp này bị khoá lại.",
      );
      const reloaded = await queue.reload();
      // The queue lists undecided drafts only, so a draft that left it after a failed submission
      // has a decision on record. Which decision, and whose, is a question for the audit timeline —
      // this cannot know whether the vanished row is the one this card tried to write.
      if (reloaded && !reviews.has(item.agent_run_id)) {
        render(
          noticeHost,
          h(
            "div",
            { class: "notice", dataState: "warn", role: "alert" },
            h("p", { class: "notice__title" }, "Bản nháp vừa rời hàng chờ"),
            h(
              "p",
              null,
              `Lượt chạy ${shortId(item.agent_run_id)} đã có một quyết định được ghi — có thể là ` +
                "của bạn, có thể của người khác. Mỗi lượt chạy chỉ nhận đúng một quyết định; hãy " +
                "xem dòng thời gian kiểm toán để biết ai đã quyết định và quyết định gì.",
            ),
          ),
        );
      }
    }
  }

  /**
   * The one confirmation a committed decision gets. It lives outside the queue because the card it
   * belongs to is gone by the time it is read.
   *
   * @param {any} decided
   * @returns {HTMLElement}
   */
  function decisionAlert(decided) {
    return inlineAlert({
      state: "ok",
      title: `Đã ghi quyết định: ${enumVi(decided.decision)}. Không có tin nhắn nào được gửi đi.`,
      body: [
        h(
          "p",
          { class: "hint" },
          "Bản gốc của agent vẫn nằm nguyên trong sổ. Dòng này là quyết định của bạn, không phải một " +
            "tin nhắn đã gửi đi.",
        ),
        techDetails([
          ["Quyết định", enumLabel(decided.decision)],
          ["Bản ghi duyệt", shortId(decided.review_id), { copy: String(decided.review_id || "") }],
          [
            "Lượt chạy agent",
            shortId(decided.agent_run_id),
            { copy: String(decided.agent_run_id || "") },
          ],
          ["Người quyết định", shortId(decided.decided_by_staff_id)],
          ["Lúc", dateTime(decided.decided_at), { mono: false }],
        ]),
      ],
    });
  }

  /**
   * The reason picker: three chips for the usual reasons and "Mã khác" for a free code, which
   * keeps the table's pattern and is upper-cased as it is typed.
   *
   * @param {Review} entry
   * @param {boolean} required
   * @param {string} prefix
   * @returns {HTMLElement}
   */
  function reasonPicker(entry, required, prefix) {
    const codeInput = /** @type {HTMLInputElement} */ (
      h("input", {
        type: "text",
        value: entry.reasonChip === OTHER_REASON ? entry.reasonCode : "",
        autocomplete: "off",
        spellcheck: "false",
        autocapitalize: "characters",
        maxlength: String(MAX_REASON),
        placeholder: "VD: KHACH_DA_DEN_LAY",
        dataFormat: "id",
        disabled: entry.busy || !verdict.allowed,
        "aria-invalid": entry.reasonCode && !REASON_CODE.test(entry.reasonCode) ? "true" : null,
        onInput: (event) => {
          entry.reasonCode = event.target.value.trim().toUpperCase();
          event.target.value = entry.reasonCode;
          event.target.setAttribute(
            "aria-invalid",
            entry.reasonCode && !REASON_CODE.test(entry.reasonCode) ? "true" : "false",
          );
          edited(entry);
        },
      })
    );
    const otherField = labelled({
      id: `${prefix}-reason`,
      label: "Mã lý do khác",
      hint: "Viết hoa, dạng A-Z 0-9 _, dài 3–64 ký tự.",
      control: codeInput,
    });
    otherField.hidden = entry.reasonChip !== OTHER_REASON;
    const chips = segmented({
      label: required ? "Lý do từ chối" : "Lý do sửa (tuỳ chọn)",
      wrap: true,
      value: entry.reasonChip,
      options: [
        ...REASON_CHIPS.map((chip) => ({ value: chip.code, label: chip.label })),
        { value: OTHER_REASON, label: "Mã khác…" },
      ],
      onChange: (value) => {
        // A second tap on the chip already chosen clears it, which is how an optional reason is
        // taken back off an edit.
        const same = value === entry.reasonChip && value !== OTHER_REASON;
        entry.reasonChip = same ? "" : value;
        chips.setValue(entry.reasonChip);
        entry.reasonCode = same ? "" : value === OTHER_REASON ? codeInput.value : value;
        otherField.hidden = entry.reasonChip !== OTHER_REASON;
        if (entry.reasonChip === OTHER_REASON) codeInput.focus();
        edited(entry);
      },
    });
    for (const chip of chips.querySelectorAll("button")) {
      const code = chip.getAttribute("data-value");
      if (code && code !== OTHER_REASON) chip.title = code;
      chip.disabled = entry.busy || !verdict.allowed;
      entry.controls.push(/** @type {HTMLElement} */ (chip));
    }
    entry.controls.push(codeInput);
    return h(
      "div",
      { class: "stack stack--tight" },
      h("p", { class: "label" }, required ? "Vì sao không dùng được?" : "Lý do sửa (tuỳ chọn)"),
      chips,
      otherField,
    );
  }

  /**
   * One of the card's action buttons, locked while busy and gated by the role.
   *
   * @param {object} spec
   * @param {string} spec.label
   * @param {string} [spec.decision] the token it writes, kept in `title` and a data attribute
   * @param {"primary"|"secondary"|"quiet"|"danger"} [spec.variant]
   * @param {() => void} spec.onClick
   * @param {Review} entry
   * @param {boolean} [spec.network]
   * @returns {HTMLElement}
   */
  function action(spec, entry) {
    const node = button({
      label: spec.label,
      variant: spec.variant,
      network: spec.network !== false,
      disabled: entry.busy,
      block: true,
      onClick: spec.onClick,
      data: spec.decision ? { decision: spec.decision } : undefined,
    });
    if (spec.decision) node.title = `${spec.decision} — ${DECISION_HINT[spec.decision]}`;
    entry.controls.push(node);
    return gated(node, verdict);
  }

  /**
   * Draw the card's action area for its current mode.
   *
   * @param {any} item
   * @param {Review} entry
   */
  function drawActions(item, entry) {
    if (!entry.actionsHost) return;
    entry.controls = [];
    const prefix = `shadow-${item.agent_run_id}`;
    const setMode = (mode) => {
      entry.mode = mode;
      // Leaving a mode takes what it held with it: an edit abandoned is not carried into a
      // "Duyệt", and a reason picked for a refusal is not carried into an edit.
      if (mode !== "edit") entry.editedText = "";
      if (mode === "idle" || mode === "edit") {
        entry.reasonChip = "";
        entry.reasonCode = "";
      }
      if (mode === "edit" && !entry.editedText) entry.editedText = String(item.draft_text || "");
      edited(entry);
      drawActions(item, entry);
      const focus = entry.actionsHost?.querySelector("textarea, .segmented button");
      if (focus instanceof HTMLElement) focus.focus();
    };

    if (entry.mode === "edit") {
      const editedInput = h("textarea", {
        rows: "5",
        maxlength: String(MAX_EDITED),
        spellcheck: "true",
        placeholder: "Viết lại nội dung sẽ gửi cho khách…",
        disabled: entry.busy || !verdict.allowed,
        onInput: (event) => {
          entry.editedText = event.target.value;
          edited(entry);
        },
      });
      editedInput.value = entry.editedText;
      entry.controls.push(editedInput);
      render(
        entry.actionsHost,
        h(
          "div",
          { class: "stack" },
          labelled({
            id: `${prefix}-edited`,
            label: "Bản bạn viết lại",
            hint:
              `Tối đa ${MAX_EDITED} ký tự. Lưu thành bản nháp mới mang tên bạn; ` +
              "bản gốc phía trên giữ nguyên.",
            control: editedInput,
          }),
          reasonPicker(entry, false, prefix),
          h(
            "div",
            { class: "draft__actions draft__actions--two" },
            action(
              { label: "Huỷ", variant: "quiet", network: false, onClick: () => setMode("idle") },
              entry,
            ),
            action(
              {
                label: "Lưu và duyệt",
                variant: "primary",
                decision: "EDIT",
                onClick: () => void decide(item, "EDIT"),
              },
              entry,
            ),
          ),
        ),
      );
      return;
    }

    if (entry.mode === "reject") {
      render(
        entry.actionsHost,
        h(
          "div",
          { class: "stack" },
          reasonPicker(entry, true, prefix),
          h(
            "div",
            { class: "draft__actions draft__actions--two" },
            action(
              { label: "Huỷ", variant: "quiet", network: false, onClick: () => setMode("idle") },
              entry,
            ),
            action(
              {
                label: "Từ chối bản nháp",
                variant: "danger",
                decision: "REJECT",
                onClick: () => void decide(item, "REJECT"),
              },
              entry,
            ),
          ),
        ),
      );
      return;
    }

    render(
      entry.actionsHost,
      h(
        "div",
        { class: "draft__actions" },
        action(
          { label: "Duyệt", decision: "APPROVE", onClick: () => void decide(item, "APPROVE") },
          entry,
        ),
        action(
          { label: "Sửa rồi duyệt", network: false, onClick: () => setMode("edit") },
          entry,
        ),
        action({ label: "Từ chối", network: false, onClick: () => setMode("reject") }, entry),
      ),
    );
  }

  /**
   * One draft: what it says, the three things a reviewer may do with it, and where it came from.
   *
   * @param {any} item
   * @returns {HTMLElement}
   */
  function draftCard(item) {
    const entry = reviewFor(item.agent_run_id);
    const line = resultLine();
    const failureHost = h("div", { class: "stack stack--tight" }, failureBlock(entry));
    const actionsHost = h("div");
    entry.line = line;
    entry.failureHost = failureHost;
    entry.actionsHost = actionsHost;

    // A queue reload rebuilds every card, so what the card was saying is restored from state rather
    // than lost. A locked card in particular must keep saying why it is locked.
    if (entry.status) setResult(line, entry.status.state, entry.status.text);
    drawActions(item, entry);

    const raw = typeof item.draft_text === "string" ? item.draft_text : "";
    return h(
      "article",
      { class: "draft surface", dataAgentRun: String(item.agent_run_id || "") },
      h(
        "div",
        { class: "draft__head" },
        statusPill({
          state: "warn",
          text: "Chờ bạn quyết",
          token: String(item.terminal_outcome || ""),
        }),
        h("span", { class: "draft__time" }, `Agent soạn lúc ${dateTime(item.produced_at)}`),
      ),
      draftBubble(item.draft_text, `${raw.length} ký tự, do agent sinh ra và chưa ai duyệt.`),
      actionsHost,
      h("p", { class: "hint draft__fact" }, "Duyệt chỉ ghi quyết định của bạn — chưa gửi gì cho khách."),
      line,
      failureHost,
      // CONTACT-PICK-001: the customer who wrote, to ＋ Nhận đồ. This list does not carry the
      // binding, so the press reads it from the draft's own binding route and hands that over.
      newOrderForDraft(store, item.agent_run_id),
      provenance(item),
    );
  }

  /** Drop the state of drafts that have left the queue, so the map cannot grow across a shift. */
  function prune(items) {
    const live = new Set(items.map((item) => item.agent_run_id));
    for (const key of [...reviews.keys()]) {
      if (!live.has(key)) reviews.delete(key);
    }
  }

  // The calm empty queue. `listView` renders a plain line for an empty list; this screen shows the
  // kit's empty state instead, in its own host, and hides the list host while there is nothing.
  const emptyHost = h(
    "div",
    null,
    emptyState({
      icon: "draft",
      title: "Không có bản nháp nào đang chờ",
      body: "Máy không tự gửi tin nào. Khi agent soạn một tin, nó sẽ nằm ở đây chờ bạn duyệt.",
    }),
  );
  emptyHost.hidden = true;

  /**
   * The queue. A read, so retrying it is offered; nothing here re-issues a decision. `reload()`
   * resolves to whether the queue on screen is now the server's current answer — the conflict path
   * in `decide` depends on that signal.
   */
  const queue = listView({
    limit: QUEUE_LIMIT,
    fetch: async () => {
      const body = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/shadow/drafts?limit=${QUEUE_LIMIT}`,
      );
      return Array.isArray(body) ? body : [];
    },
    renderItem: draftCard,
    emptyText: "Không có bản nháp nào đang chờ quyết định trong cửa hàng này.",
    skeletonRows: 2,
    clearMetaOnError: true,
    truncationText: (limit) =>
      `Máy chủ trả tối đa ${limit} bản nháp và đã trả đủ; có thể còn nữa. API này không có phân trang.`,
    onLoadStart: () => {
      emptyHost.hidden = true;
      queueHost.hidden = false;
      // The cards about to be replaced no longer own the nodes their entries point at.
      for (const entry of reviews.values()) {
        entry.line = null;
        entry.failureHost = null;
        entry.actionsHost = null;
      }
    },
    onLoaded: (items) => {
      prune(items);
      emptyHost.hidden = items.length > 0;
      queueHost.hidden = items.length === 0;
    },
  });
  const queueHost = queue.host;

  /* ---- the review log ---------------------------------------------------------------------
   *
   * The queue above is undecided-only, so a draft leaves it the instant somebody rules on it. That
   * is right for a queue and wrong for a record: approve, edit and reject are captured so the agent
   * can be graded on them, and this compact history is where they are read back. An approved or
   * rewritten draft is also where a person asks for it to be sent — one link, to `#/exceptions`.
   */

  const REVIEW_LIMIT = 50;
  let oldestReviewId = null;
  const reviewLogHost = h("div");
  const reviewMoreHost = h("div");

  /**
   * One decided draft, compact: what was decided and when, the words it ended on, and — for an
   * approved or rewritten one — the one way to ask for it to be sent. The full words and the ids
   * sit in the drawer underneath.
   *
   * @param {any} item
   * @returns {HTMLElement}
   */
  function reviewedRow(item) {
    const decision = String(item.decision || "");
    const sendable = decision === "APPROVE" || decision === "EDIT";
    const words =
      typeof item.edited_text === "string" && item.edited_text ? item.edited_text : item.draft_text;
    return h(
      "div",
      { class: "history", dataDecision: decision },
      h(
        "div",
        { class: "history__head" },
        statusPill({
          state: decision === "REJECT" ? "neutral" : "ok",
          text: enumVi(decision),
          token: decision,
        }),
        h("span", { class: "history__time" }, dateTime(item.decided_at)),
        item.reason_code
          ? h(
              "span",
              { class: "history__reason", title: String(item.reason_code) },
              REASON_CHIPS.find((chip) => chip.code === item.reason_code)?.label ||
                String(item.reason_code),
            )
          : null,
      ),
      h("p", { class: "history__snippet" }, typeof words === "string" && words ? words : UNKNOWN),
      // MESSAGE-DRAFT-BINDING-001. A draft somebody approved or rewrote is one a person may now
      // ask to send, and that request is raised on `#/exceptions` from the server's own binding —
      // this link only carries the draft id there. A rejected draft has nothing sendable, and the
      // server answers its binding with a 404, so no link is offered for it.
      sendable
        ? linkButton({
            href: `#/exceptions?draft=${encodeURIComponent(String(item.agent_run_id || ""))}`,
            label: "Xin duyệt gửi tay",
            variant: "quiet",
            icon: "message",
          })
        : null,
      // CONTACT-PICK-001. Only where the binding route answers: a rejected draft reads as 404.
      sendable ? newOrderForDraft(store, item.agent_run_id) : null,
      techDetails(
        [
          [
            "Bản agent soạn",
            draftBubble(item.draft_text, `Agent ghi lúc ${dateTime(item.produced_at)}`),
            { mono: false },
          ],
          item.edited_text
            ? [
                "Người sửa đã viết lại",
                messageBubble({
                  text: item.edited_text,
                  side: "out",
                  emptyText: `${UNKNOWN} bản sửa không có chữ nào`,
                }),
                { mono: false },
              ]
            : null,
          ["Quyết định", enumLabel(decision)],
          item.reason_code ? ["Mã lý do", item.reason_code] : null,
          [
            "Lượt chạy agent",
            shortId(item.agent_run_id),
            { copy: String(item.agent_run_id || "") },
          ],
          ["Người quyết định", shortId(item.decided_by_staff_id)],
        ],
        { summary: "Toàn văn và chi tiết" },
      ),
    );
  }

  /**
   * @param {boolean} more
   */
  function renderReviewMore(more) {
    if (!more) {
      render(reviewMoreHost);
      return;
    }
    render(
      reviewMoreHost,
      h(
        "div",
        { class: "stack stack--tight history__more" },
        h("p", { class: "hint" }, "Đang hiện các quyết định gần nhất; còn quyết định cũ hơn."),
        button({
          label: "Xem thêm quyết định cũ hơn",
          variant: "quiet",
          network: true,
          onClick: (event) => void loadOlderReviews(/** @type {any} */ (event.currentTarget)),
        }),
      ),
    );
  }

  /** @type {HTMLElement|null} */
  let reviewList = null;

  /**
   * @param {HTMLElement|null} control
   */
  async function loadOlderReviews(control) {
    if (!store || !oldestReviewId) return;
    if (control) control.disabled = true;
    try {
      const body = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/shadow/reviews` +
          `?limit=${REVIEW_LIMIT}&before=${encodeURIComponent(oldestReviewId)}`,
      );
      const items = Array.isArray(body) ? body : [];
      if (items.length > 0) {
        oldestReviewId = String(items[items.length - 1].review_id || "");
        // Appended, not re-rendered: this list grows downward and older entries belong below, so
        // nothing already on screen moves under the reader.
        reviewList?.append(
          ...items.map((item) => h("li", { class: "rows__item" }, reviewedRow(item))),
        );
      }
      renderReviewMore(isTruncated(items, REVIEW_LIMIT));
    } catch (error) {
      if (control) control.disabled = false;
      render(reviewMoreHost, errorNotice(error, { onRetry: () => void loadOlderReviews(null) }));
    }
  }

  async function loadReviews() {
    if (!store) return;
    render(reviewLogHost, skeleton(2));
    render(reviewMoreHost);
    try {
      const body = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/shadow/reviews?limit=${REVIEW_LIMIT}`,
      );
      const items = Array.isArray(body) ? body : [];
      oldestReviewId = items.length > 0 ? String(items[items.length - 1].review_id || "") : null;
      reviewList = items.length ? list(items.map(reviewedRow), { label: "Đã quyết" }) : null;
      render(
        reviewLogHost,
        reviewList || h("p", { class: "empty-line" }, "Chưa có quyết định nào được ghi."),
      );
      renderReviewMore(isTruncated(items, REVIEW_LIMIT));
    } catch (error) {
      render(reviewLogHost, errorNotice(error, { onRetry: () => void loadReviews() }));
    }
  }

  void queue.reload();
  void loadReviews();

  return h(
    "section",
    { class: "screen" },
    page({
      title: "Bản nháp AI",
      subtitle: "Máy không tự gửi — mỗi tin chờ bạn quyết.",
      info: houseRules(),
    }),
    decisionHost,
    noticeHost,
    section({
      title: /** @type {any} */ (h("span", { class: "row" }, "Chờ bạn quyết", queue.count)),
      card: false,
      children: h(
        "div",
        { class: "stack" },
        queue.bar.node,
        queue.truncation,
        emptyHost,
        queueHost,
      ),
    }),
    section({
      title: "Đã quyết",
      card: false,
      info: infoButton(
        "Danh sách này là gì?",
        h(
          "p",
          { class: "hint" },
          "Đây là những bản nháp đã có người quyết định. Bản gốc của agent nằm cạnh quyết định để " +
            "sau này chấm được agent bằng việc thật. Không dòng nào ở đây là một tin nhắn đã gửi đi.",
        ),
      ),
      children: h("div", { class: "stack" }, reviewLogHost, reviewMoreHost),
    }),
  );
}

export const screen = {
  path: "/shadow",
  title: "Bản nháp AI",
  capability: "SHADOW_READ",
  needsStore: true,
  render: render_,
};
