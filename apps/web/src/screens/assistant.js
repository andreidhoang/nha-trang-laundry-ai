/**
 * Trợ lý AI: the owner-assistant chat, a thin window onto a deterministic server brain.
 *
 * Everything this screen shows was decided on the server. The assistant there matches a question
 * against a fixed intent table and answers from governed reads; it never calls a model and never
 * computes money, policy or order state. This screen's job is to carry the question over, render
 * the answer honestly, and keep the operator's half-typed text safe while doing so.
 *
 * What this screen deliberately does NOT do:
 *
 *   - **No model and no token generation.** The answer is fully decided and persisted by the POST
 *     that records the turn. The typewriter effect is the server pacing that already-durable text
 *     over SSE (`.../turns/{id}/stream`) so it reads progressively — transport pacing, not a model
 *     thinking. If the stream cannot run (no EventSource, a network drop), the full answer from
 *     the POST response renders whole instead; a half-answer is never the final state.
 *   - **No client-side intent logic.** The browser never classifies a question; the same text
 *     must get the same answer whoever asks from wherever, so matching lives in one place.
 *   - **No local persistence.** The transcript is server-side history (`assistant_turns`), loaded
 *     on entry. Nothing is written to the device — a counter phone is shared and often unlocked.
 *   - **No money.** The assistant cannot answer revenue questions and this screen carries no
 *     amount field at all, so there is nothing here to misrender.
 *   - **No raw HTML.** Streamed deltas are appended as text nodes only, through the same safe
 *     `h()`/`render()` builders as everything else.
 *
 * Three interaction rules are tested by `scripts/verify_console_interaction.py`:
 *
 *   - The composer node is stable. Transcript updates render into a separate host, so typing,
 *     focus and caret survive a submit, the stream that follows it, and every rebuild.
 *   - Enter sends; Shift+Enter inserts a newline. The composer stays a real `<form>`, so the
 *     printed record keeps the transcript and drops the controls.
 *   - A write is never retried automatically. A failed POST renders the server's refusal; only a
 *     human pressing Gửi again issues another turn, under a fresh idempotency key if the text
 *     changed.
 *
 * @module screens/assistant
 */

import { Submission, request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { dateTime, shortId } from "../core/format.js";
import { NAV, enumVi } from "../core/i18n.js";
import { storeId } from "../core/session.js";
import { badge, errorNotice, icon, reasonCodeList, skeleton } from "../ui/components.js";

/** The server caps history at 100; ask for its default page. */
const HISTORY_LIMIT = 50;

/** `assistant_turns.question` is `char_length <= 4000`; refuse earlier and in words. */
const MAX_QUESTION = 4000;

/** Starters for the empty transcript. Each one maps to a documented intent of the brain. */
const SUGGESTIONS = [
  "Hôm nay thế nào?",
  "Đơn nào sắp trễ hẹn?",
  "Có gì chờ duyệt không?",
  "Bạn trả lời được gì?",
];

/**
 * The intent token as a badge: Vietnamese gloss leading, the verbatim token beside it.
 *
 * @param {string} intent
 * @returns {HTMLElement}
 */
function intentBadge(intent) {
  return badge({ token: enumVi(intent), gloss: "", state: "info", title: intent });
}

/**
 * A turn's small print: which recorded turn this is and when, in business time.
 *
 * @param {any} item
 * @returns {HTMLElement}
 */
function provenance(item) {
  return h(
    "p",
    { class: "hint mono" },
    `lượt ${shortId(item.turn_id)} · ${dateTime(item.created_at)}`,
  );
}

/**
 * Multi-line text as paragraphs. Every line is a text node; nothing is ever interpreted.
 *
 * @param {string} text
 * @returns {HTMLElement[]}
 */
function paragraphs(text) {
  return String(text)
    .split(/\r?\n/)
    .filter((line) => line.trim() !== "")
    .map((line) => h("p", null, line));
}

/**
 * One of the operator's questions, rendered as their own words in a right-aligned bubble.
 *
 * @param {string} question
 * @returns {HTMLElement}
 */
function questionBubble(question) {
  return h(
    "article",
    { class: "chat__user" },
    h("p", { class: "eyebrow" }, "Bạn hỏi"),
    ...paragraphs(question),
  );
}

/**
 * The deep links the server attached to an answer, as plain anchors into this console's routes.
 *
 * @param {any} item
 * @returns {HTMLElement|null}
 */
function linkRow(item) {
  const links = Array.isArray(item.links) ? item.links : [];
  if (!links.length) return null;
  return h(
    "div",
    { class: "form__actions" },
    links.map((link) =>
      h("a", { class: "button", href: String(link.href || "#/") }, String(link.label || "Mở")),
    ),
  );
}

/**
 * One assistant answer rendered whole — history items and the stream fallback take this path.
 *
 * @param {any} item
 * @returns {HTMLElement}
 */
function answerBlock(item) {
  return h(
    "article",
    { class: "chat__assistant" },
    h(
      "div",
      { class: "spread" },
      h("p", { class: "eyebrow" }, "Trợ lý AI"),
      intentBadge(String(item.intent || "")),
    ),
    h("div", { class: "chat__answer stack stack--tight" }, paragraphs(item.answer || "")),
    reasonCodeList(Array.isArray(item.reason_codes) ? item.reason_codes : []),
    linkRow(item),
    provenance(item),
  );
}

/**
 * A live assistant answer: typing dots while the POST is in flight, then the persisted answer
 * replayed word by word with a trailing caret, then the badge, links and provenance.
 *
 * The stream carries no content of its own — every frame is a slice of the answer the POST
 * already returned and the server already stored. Any failure on the stream falls back to that
 * full answer, so the transcript never ends at a partial string.
 *
 * @param {string} store
 * @returns {{card: HTMLElement, begin: (turn: any) => void}}
 */
function liveAnswer(store) {
  const badgeHost = h("div");
  const dots = h(
    "span",
    { class: "chat__dots", role: "status" },
    h("span", { class: "sr-only" }, "Trợ lý đang trả lời…"),
    h("span", { "aria-hidden": "true" }),
    h("span", { "aria-hidden": "true" }),
    h("span", { "aria-hidden": "true" }),
  );
  const answerNode = h("span", { class: "chat__answer chat__answer--live" });
  const caret = h("span", { class: "chat__caret", "aria-hidden": "true" });
  const body = h("div", { class: "chat__body" }, dots);
  const footer = h("div", { class: "stack stack--tight" });
  const card = h(
    "article",
    { class: "chat__assistant" },
    h("div", { class: "spread" }, h("p", { class: "eyebrow" }, "Trợ lý AI"), badgeHost),
    body,
    footer,
  );

  /**
   * The finishing chrome every complete answer gets, however the text arrived.
   *
   * @param {any} turn
   */
  function finishChrome(turn) {
    render(badgeHost, intentBadge(String(turn.intent || "")));
    render(
      footer,
      reasonCodeList(Array.isArray(turn.reason_codes) ? turn.reason_codes : []),
      linkRow(turn),
      provenance(turn),
    );
  }

  /**
   * The stream could not run or broke mid-way: render the whole answer from the POST response.
   *
   * @param {any} turn
   */
  function renderWhole(turn) {
    render(
      body,
      h("div", { class: "chat__answer stack stack--tight" }, paragraphs(turn.answer || "")),
    );
    finishChrome(turn);
  }

  /**
   * The POST resolved: swap the typing dots for the streaming surface and open the replay.
   *
   * @param {any} turn
   */
  function begin(turn) {
    render(body, answerNode, caret);
    if (typeof EventSource !== "function") {
      renderWhole(turn);
      return;
    }
    const source = new EventSource(
      `/internal/v1/stores/${encodeURIComponent(store)}/assistant/turns/${encodeURIComponent(String(turn.turn_id))}/stream`,
    );
    let settled = false;
    source.onmessage = (event) => {
      if (event.data === "[DONE]") {
        settled = true;
        source.close();
        caret.remove();
        finishChrome(turn);
        return;
      }
      try {
        answerNode.appendChild(document.createTextNode(JSON.parse(event.data)));
      } catch {
        // A frame that is not a JSON string carries nothing worth keeping; the full answer is
        // still one fallback away if the stream later breaks.
      }
    };
    source.onerror = () => {
      if (settled) return;
      settled = true;
      source.close();
      renderWhole(turn);
    };
  }

  return { card, begin };
}

/**
 * The empty transcript: a welcome and a few questions the brain is documented to answer.
 * Picking a chip fills the composer and sends it through the same path as a typed question.
 *
 * @param {(question: string) => void} onPick
 * @returns {HTMLElement}
 */
function welcome(onPick) {
  return h(
    "div",
    { class: "chat__empty" },
    h("p", { class: "chat__welcome" }, "Hỏi trợ lý về cửa hàng"),
    h(
      "p",
      { class: "hint" },
      "Chưa có câu hỏi nào được ghi cho cửa hàng này. Bắt đầu bằng một trong những câu dưới, " +
        "hoặc tự viết câu hỏi của bạn.",
    ),
    h(
      "div",
      { class: "chat__chips" },
      SUGGESTIONS.map((question) =>
        h(
          "button",
          {
            type: "button",
            class: "chip",
            dataRequiresNetwork: "true",
            onClick: () => onPick(question),
          },
          question,
        ),
      ),
    ),
  );
}

/**
 * @returns {HTMLElement}
 */
export function render_() {
  const store = storeId();
  const submission = new Submission("assistant-turn");

  const transcriptHost = h("div", { class: "chat__transcript" }, skeleton(2));
  const noticeHost = h("div");

  /**
   * The composer is built once and never re-created. Transcript and failure updates touch their
   * own hosts, so what the operator typed — and where their focus is — survives everything,
   * including the answer streaming in beside them.
   */
  const textarea = h("textarea", {
    id: "assistant-question",
    rows: "1",
    maxlength: String(MAX_QUESTION),
    spellcheck: "true",
    placeholder: "Hỏi về tình hình hôm nay, đơn có nguy cơ trễ, hàng chờ duyệt…",
    dataRequiresNetwork: "true",
    "aria-label": "Câu hỏi cho trợ lý AI",
    onInput: () => {
      submission.reset();
      autoGrow();
    },
    onKeydown: (event) => {
      // Enter sends, Shift+Enter inserts a newline — the chat convention an operator expects.
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        form.requestSubmit();
      }
    },
  });
  const submit = h(
    "button",
    {
      type: "submit",
      class: "chat__send",
      dataVariant: "primary",
      dataRequiresNetwork: "true",
      title: "Gửi",
    },
    icon("arrow-up"),
    h("span", { class: "sr-only" }, "Gửi"),
  );

  /** Grow the textarea with its content; the CSS max-height caps it near eight rows. */
  function autoGrow() {
    textarea.style.height = "auto";
    textarea.style.height = `${textarea.scrollHeight}px`;
  }

  /**
   * @param {boolean} busy
   */
  function setBusy(busy) {
    textarea.disabled = busy;
    submit.disabled = busy;
  }

  /**
   * Ask one question. Called by the form's submit or a suggestion chip: no timer, no observer,
   * no retry path reaches this function, so a turn is recorded exactly when a human asked for it.
   */
  async function ask() {
    const question = textarea.value.trim();
    if (!question || !store) {
      render(
        noticeHost,
        h(
          "div",
          { class: "notice", dataState: "warn", role: "alert" },
          h("p", { class: "notice__title" }, "Chưa có câu hỏi"),
          h("p", null, "Viết câu hỏi rồi bấm Gửi. Không có gì được ghi lại."),
        ),
      );
      return;
    }

    setBusy(true);
    render(noticeHost);
    // The question and the pending answer appear immediately, so the operator sees their words
    // land while the server records the turn.
    if (transcriptHost.querySelector(".chat__empty")) render(transcriptHost);
    const bubble = questionBubble(question);
    const live = liveAnswer(store);
    transcriptHost.append(bubble, live.card);
    live.card.scrollIntoView({ block: "nearest" });
    try {
      const turn = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/assistant/turns`,
        {
          method: "POST",
          body: { question },
          idempotencyKey: submission.key(),
        },
      );
      submission.reset();
      textarea.value = "";
      autoGrow();
      live.begin(turn);
    } catch (error) {
      // No retry is offered on a write: a timeout may have committed the turn, and the history
      // load below is where the truth gets established — deliberately, by a person. The pending
      // pair comes down because nothing is known to be recorded.
      bubble.remove();
      live.card.remove();
      render(noticeHost, errorNotice(error));
    } finally {
      setBusy(false);
      textarea.focus();
    }
  }

  const form = h(
    "form",
    {
      class: "chat__composer",
      onSubmit: (event) => {
        event.preventDefault();
        void ask();
      },
    },
    h("div", { class: "chat__composer-row" }, textarea, submit),
    h(
      "p",
      { class: "hint chat__composer-hint" },
      `Enter để gửi · Shift+Enter xuống dòng · Tối đa ${MAX_QUESTION} ký tự.`,
    ),
  );

  /**
   * Load the recorded history. The server returns newest first; the transcript renders
   * oldest-first so it reads like a conversation. History answers render whole — the typewriter
   * replay is for the turn the operator just asked, not for old rows. A read, so retrying it is
   * honest.
   */
  async function loadHistory() {
    if (!store) {
      // The shell re-renders this screen once the store scope resolves; fetching with a
      // null store id would only produce a refused request.
      render(transcriptHost, skeleton(2));
      return;
    }
    render(transcriptHost, skeleton(2));
    try {
      const body = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/assistant/turns?limit=${HISTORY_LIMIT}`,
      );
      const items = Array.isArray(body) ? body : [];
      render(
        transcriptHost,
        items.length === 0
          ? welcome((question) => {
              textarea.value = question;
              submission.reset();
              autoGrow();
              void ask();
            })
          : [...items]
              .reverse()
              .map((item) => [questionBubble(item.question), answerBlock(item)]),
      );
    } catch (error) {
      render(transcriptHost, errorNotice(error, { onRetry: () => void loadHistory() }));
    }
  }

  void loadHistory();

  return h(
    "section",
    { class: "screen" },
    h(
      "div",
      { class: "screen__header" },
      h("p", { class: "eyebrow" }, "Giám sát AI"),
      h("h1", null, NAV.assistant),
      h(
        "p",
        { class: "screen__lede" },
        "Hỏi về tình hình cửa hàng bằng tiếng Việt. Mọi câu trả lời chỉ đến từ dữ liệu vận hành " +
          "hệ thống đang quản lý — điều gì nó không biết, nó nói là không biết, và không bao giờ " +
          "đoán một con số.",
      ),
    ),
    h(
      "div",
      { class: "notice", dataState: "info" },
      h(
        "p",
        null,
        "Trợ lý này không gọi mô hình ngôn ngữ và không tính tiền. Mỗi câu trả lời được máy " +
          "chủ quyết định và ghi lại trước, rồi hiện dần trên màn hình — chữ chạy là nhịp hiển " +
          "thị của câu trả lời đã lưu, không phải mô hình đang sinh từ. Mọi lượt hỏi đều được " +
          "ghi lại kèm người hỏi.",
      ),
    ),
    h(
      "div",
      { class: "chat" },
      h("div", { class: "chat__col" }, noticeHost, transcriptHost, form),
    ),
  );
}

export const screen = {
  path: "/assistant",
  title: NAV.assistant,
  capability: "ASSISTANT",
  needsStore: true,
  render: render_,
};
