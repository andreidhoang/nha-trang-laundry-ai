/**
 * Shadow: the human review surface for everything the agent proposes.
 *
 * Nothing on this screen sends anything. The queue is the set of drafts the agent produced and that
 * no person has ruled on yet, and the only thing a decision writes is one attributed review row.
 * Several choices here look defensive and are:
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
 *     text in its own block so that it never reads as something the console is telling the operator.
 *
 * The decision vocabulary is `APPROVE` / `EDIT` / `REJECT`. The approvals queue uses `APPROVED` /
 * `REJECTED` for a different aggregate entirely; the two are never mixed, which is why the tokens
 * are printed on the buttons rather than translated into Vietnamese verbs.
 *
 * @module screens/shadow
 */

import { Submission, request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { UNKNOWN, dateTime, integer, shortId } from "../core/format.js";
import { enumLabel } from "../core/i18n.js";
import { can } from "../core/rbac.js";
import { principal, storeId } from "../core/session.js";
import {
  errorNotice,
  facts,
  gated,
  labelled,
  listView,
  panel,
  resultLine,
  revealError,
  setResult,
  warningBadges,
} from "../ui/components.js";

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

/** The three values `decision` may take, verbatim. */
const DECISIONS = ["APPROVE", "EDIT", "REJECT"];

/** What each button does, in the reviewer's language, with the token kept in view. */
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
 * @property {string} reasonCode
 * @property {string} editedText
 * @property {boolean} busy the card has submitted a decision and must not submit a second one
 * @property {{state: "ok"|"warn"|"danger", text: string}|null} status
 * @property {any} failure the last `ApiError`, kept so a queue reload does not erase it
 * @property {Submission} submission
 * @property {HTMLElement|null} line the card's result line, while the card is on screen
 * @property {HTMLElement|null} failureHost where that card renders its failure, while on screen
 * @property {HTMLElement[]} controls everything that must be disabled while the card is busy
 */

/**
 * The draft body, in its own block.
 *
 * Line breaks are preserved by splitting into paragraphs rather than by styling whitespace, so a
 * multi-line message reads the way it was written. The character count is shown because channel
 * limits are real and a reviewer editing a long draft needs to know what they started from.
 *
 * @param {string} text
 * @returns {HTMLElement}
 */
function draftBody(text) {
  const raw = typeof text === "string" ? text : "";
  const lines = raw.split(/\r?\n/).filter((line) => line.trim() !== "");
  return h(
    "div",
    { class: "notice" },
    h("p", { class: "eyebrow" }, "Nội dung bản nháp · Văn bản không tin cậy"),
    h(
      "div",
      { class: "stack stack--tight" },
      lines.length
        ? lines.map((line) => h("p", null, line))
        : h("p", { class: "hint" }, `${UNKNOWN} bản nháp không có chữ nào`),
    ),
    h("p", { class: "hint" }, `${raw.length} ký tự, do agent sinh ra và chưa ai duyệt.`),
  );
}

/**
 * Where a draft came from. `FR-APR-006` requires a draft to be traceable to its agent run, so both
 * identifiers are shown in full in a title attribute and shortened only for reading.
 *
 * @param {any} item
 * @returns {HTMLElement}
 */
function provenance(item) {
  return facts([
    ["Kết quả lượt chạy", enumLabel(item.terminal_outcome), { mono: true }],
    ["Mã kết quả", enumLabel(item.terminal_code), { mono: true }],
    ["Số lần gọi công cụ", integer(item.tool_call_count)],
    ["Agent ghi lúc", dateTime(item.produced_at)],
    [
      "Lượt chạy agent",
      h("span", { title: item.agent_run_id || "" }, shortId(item.agent_run_id)),
      { mono: true, span: true },
    ],
    [
      "Ràng buộc hội thoại",
      h(
        "span",
        { title: item.conversation_binding_id || "" },
        shortId(item.conversation_binding_id),
      ),
      { mono: true, span: true },
    ],
  ]);
}

/**
 * The one confirmation a committed decision gets.
 *
 * It lives outside the queue because the card it belongs to is gone by the time it is read: the
 * queue lists undecided drafts only, so a decided draft leaves the list on the very next load.
 *
 * @param {any} decided
 * @param {HTMLElement} line the screen-level result line
 * @returns {HTMLElement}
 */
function decisionPanel(decided, line) {
  return panel({
    eyebrow: "Đã ghi",
    title: "Quyết định gần nhất của bạn",
    children: h(
      "div",
      { class: "stack" },
      line,
      facts([
        [
          "Bản ghi duyệt",
          h("span", { title: decided.review_id || "" }, shortId(decided.review_id)),
          { mono: true },
        ],
        ["Quyết định", enumLabel(decided.decision), { mono: true }],
        [
          "Lượt chạy agent",
          h("span", { title: decided.agent_run_id || "" }, shortId(decided.agent_run_id)),
          { mono: true, span: true },
        ],
        [
          "Người quyết định",
          h(
            "span",
            { title: decided.decided_by_staff_id || "" },
            shortId(decided.decided_by_staff_id),
          ),
          { mono: true },
        ],
        ["Lúc", dateTime(decided.decided_at)],
      ]),
      h(
        "p",
        { class: "hint" },
        "Bản gốc của agent vẫn nằm nguyên trong sổ. Dòng này là quyết định của bạn, không phải một " +
          "tin nhắn đã gửi đi.",
      ),
    ),
  });
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

/** The standing rules of this screen, stated once above the queue. */
function houseRules() {
  return h(
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
        "EDIT tạo một bản nháp mới mang tên người sửa. Bản gốc của agent không bao giờ bị sửa đè, " +
          "nhờ vậy phần bị sửa và phần bị từ chối còn dùng được để đánh giá agent về sau.",
      ),
      h(
        "li",
        null,
        "Phần chữ trong khung riêng của mỗi thẻ là văn bản không tin cậy: do khách viết hoặc do mô " +
          "hình sinh ra. Đọc nó như dữ liệu cần kiểm, không phải như lời của hệ thống.",
      ),
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

  const decisionHost = h("div");
  const noticeHost = h("div");
  const decisionLine = resultLine();

  /**
   * @param {string} agentRunId
   * @returns {Review}
   */
  function reviewFor(agentRunId) {
    let entry = reviews.get(agentRunId);
    if (!entry) {
      entry = {
        reasonCode: "",
        editedText: "",
        busy: false,
        status: null,
        failure: null,
        submission: new Submission("shadow-decision"),
        line: null,
        failureHost: null,
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
    for (const control of entry.controls) control.disabled = false;
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
    const edited = entry.editedText;

    if (decision === "EDIT") {
      if (!edited.trim()) {
        return "EDIT phải kèm nội dung thay thế. Hãy viết bản nháp mới vào ô sửa, hoặc chọn APPROVE.";
      }
      if (edited.length > MAX_EDITED) {
        return `Nội dung sửa dài ${edited.length} ký tự, quá mức ${MAX_EDITED} máy chủ nhận.`;
      }
      if (edited.trim() === String(original || "").trim()) {
        return (
          "Nội dung sửa đang giống hệt bản gốc. Một bản EDIT không thay đổi gì thì về sau không nói " +
          "lên điều gì — hãy sửa nội dung, hoặc chọn APPROVE."
        );
      }
    } else if (edited.trim()) {
      return (
        `Ô sửa đang có nội dung nhưng ${decision} không mang nội dung sửa đi được. Bấm EDIT để ghi ` +
        "bản sửa đó, hoặc xoá ô sửa rồi quyết định lại."
      );
    }

    if (decision === "REJECT" && !reason) {
      return (
        "REJECT phải kèm mã lý do. Một lần từ chối không có lý do là một dòng lịch sử không dùng " +
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
   * @param {string} decision
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
    say(entry, "warn", `Đang ghi quyết định ${decision}…`);

    const reason = entry.reasonCode.trim();
    const payload = {
      decision,
      reason_code: reason || null,
      // The table refuses replacement text on anything but an EDIT, so the field is null-by-rule
      // rather than null-by-omission — and `validate` already refused an APPROVE or a REJECT that
      // would silently throw away something the reviewer typed.
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
      setResult(
        decisionLine,
        "ok",
        `Đã ghi quyết định ${decided.decision}. Không có tin nhắn nào được gửi đi.`,
      );
      render(decisionHost, decisionPanel(decided, decisionLine));
      await queue.reload();
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
   * One draft: where it came from, what it says, and the three things a reviewer may do with it.
   *
   * @param {any} item
   * @returns {HTMLElement}
   */
  function draftCard(item) {
    const entry = reviewFor(item.agent_run_id);
    const prefix = `shadow-${item.agent_run_id}`;
    const line = resultLine();
    const failureHost = h("div", { class: "stack stack--tight" }, failureBlock(entry));
    entry.line = line;
    entry.failureHost = failureHost;
    entry.controls = [];

    // A queue reload rebuilds every card, so what the card was saying is restored from state rather
    // than lost. A locked card in particular must keep saying why it is locked.
    if (entry.status) setResult(line, entry.status.state, entry.status.text);

    const locked = entry.busy || !verdict.allowed;

    const reasonInput = h("input", {
      type: "text",
      value: entry.reasonCode,
      autocomplete: "off",
      spellcheck: "false",
      maxlength: String(MAX_REASON),
      placeholder: "TONE_OFF_POLICY",
      dataFormat: "id",
      disabled: locked,
      "aria-invalid": entry.reasonCode && !REASON_CODE.test(entry.reasonCode) ? "true" : null,
      onInput: (event) => {
        entry.reasonCode = event.target.value.trim().toUpperCase();
        event.target.value = entry.reasonCode;
        event.target.setAttribute(
          "aria-invalid",
          entry.reasonCode && !REASON_CODE.test(entry.reasonCode) ? "true" : "false",
        );
        entry.submission.reset();
        say(entry, null, "");
        if (!entry.busy && entry.failure) showFailure(entry, null);
      },
    });

    const editedInput = h("textarea", {
      rows: "6",
      maxlength: String(MAX_EDITED),
      spellcheck: "true",
      placeholder: "Viết lại nội dung sẽ gửi cho khách…",
      disabled: locked,
      onInput: (event) => {
        entry.editedText = event.target.value;
        entry.submission.reset();
        say(entry, null, "");
        if (!entry.busy && entry.failure) showFailure(entry, null);
      },
    });
    editedInput.value = entry.editedText;

    entry.controls.push(reasonInput, editedInput);

    // Three plain buttons, deliberately of equal weight. Making APPROVE the big coloured one would
    // put a thumb on the scale of a review whose whole point is that a person chose.
    const buttons = DECISIONS.map((decision) => {
      const button = h(
        "button",
        {
          type: "button",
          disabled: entry.busy,
          title: DECISION_HINT[decision],
          onClick: () => void decide(item, decision),
        },
        decision,
      );
      entry.controls.push(button);
      return gated(button, verdict);
    });

    return h(
      "article",
      { class: "card stack" },
      h(
        "div",
        { class: "spread" },
        h(
          "strong",
          { class: "mono", title: item.agent_run_id || "" },
          shortId(item.agent_run_id),
        ),
        item.terminal_outcome === "REQUIRE_HUMAN"
          ? warningBadges(["HUMAN_APPROVAL_REQUIRED"])
          : null,
      ),
      provenance(item),
      draftBody(item.draft_text),
      h(
        "form",
        { class: "form", onSubmit: (event) => event.preventDefault() },
        labelled({
          id: `${prefix}-reason`,
          label: "Mã lý do",
          hint: "Bắt buộc với REJECT, tuỳ chọn với APPROVE và EDIT. Viết hoa, dạng A-Z 0-9 _.",
          control: reasonInput,
        }),
        labelled({
          id: `${prefix}-edited`,
          label: "Nội dung sửa",
          hint:
            `Chỉ đi kèm EDIT, tối đa ${MAX_EDITED} ký tự. Lưu thành bản nháp mới mang tên bạn; ` +
            "bản gốc phía trên giữ nguyên.",
          control: editedInput,
        }),
        h("div", { class: "form__actions" }, buttons),
        line,
        failureHost,
      ),
    );
  }

  /** Drop the state of drafts that have left the queue, so the map cannot grow across a shift. */
  function prune(items) {
    const live = new Set(items.map((item) => item.agent_run_id));
    for (const key of [...reviews.keys()]) {
      if (!live.has(key)) reviews.delete(key);
    }
  }

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
      // The cards about to be replaced no longer own the nodes their entries point at.
      for (const entry of reviews.values()) {
        entry.line = null;
        entry.failureHost = null;
      }
    },
    onLoaded: prune,
  });

  void queue.reload();

  return h(
    "section",
    { class: "screen" },
    h(
      "div",
      { class: "screen__header" },
      h("p", { class: "eyebrow" }, "Người quyết định · Máy không tự gửi"),
      h("h1", null, "Bản nháp AI"),
      h(
        "p",
        { class: "screen__lede" },
        "Mọi thứ agent soạn ra dừng lại ở đây. Màn hình này ghi lại quyết định của bạn về từng bản " +
          "nháp; nó không gửi tin nhắn nào cho khách.",
      ),
    ),
    decisionHost,
    noticeHost,
    panel({
      eyebrow: "Chờ người quyết định",
      title: "Bản nháp chưa ai xử lý",
      count: queue.count,
      guardrail:
        "Duyệt ở đây không phải là gửi. Một bản nháp rời khỏi hàng chờ này ngay khi có người quyết " +
        "định, và mỗi lượt chạy chỉ nhận đúng một quyết định.",
      children: h(
        "div",
        { class: "stack" },
        queue.bar.node,
        houseRules(),
        queue.truncation,
        queue.host,
      ),
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
