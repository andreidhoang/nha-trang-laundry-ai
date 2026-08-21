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

import { Submission, isTruncated, request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { dateTime, shortId } from "../core/format.js";
import { NAV, enumLabel } from "../core/i18n.js";
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
 * The two intents the brain emits when it is declining rather than answering.
 *
 * Both sentences the server sends for these hand the next move back to a person:
 * `REVENUE_UNAVAILABLE` names an owner policy decision that has not been ratified, and
 * `UNSUPPORTED` returns the question with the list of what would have worked. That is what `warn`
 * means in `tokens.css` — "a thing waiting on a named human" — and it is already the state
 * `ui/components.js` gives a `REQUIRE_HUMAN` refusal. Rendering these `info`, the state reserved
 * for "true but not final", made a refusal and an answer look identical on the one screen whose
 * job is telling those apart.
 *
 * `ORDER_LOOKUP` is deliberately absent. The brain emits it whether the order was found or not,
 * so separating the two here would mean reading the answer text — client-side intent logic by
 * another name. The badge reports the server's decision; it never forms one.
 */
const DECLINED_INTENTS = new Set(["REVENUE_UNAVAILABLE", "UNSUPPORTED"]);

/**
 * How the server read the question, labelled as a claim the operator can disagree with.
 *
 * Two changes from the bare badge this replaced. The token renders `Gloss (TOKEN)` through
 * `enumLabel`, the dual-language rule every other server enum on this console follows; the earlier
 * form passed the gloss as `token` with an empty `gloss`, which pushed the verbatim token into a
 * `title` tooltip — unreachable on touch, and this console is used on a phone at a counter. And
 * the chip is introduced by a word, because an unlabelled chip beside an answer reads as
 * decoration rather than as the machine stating how it understood the question.
 *
 * @param {string} intent
 * @returns {HTMLElement}
 */
function intentRow(intent) {
  return h(
    "div",
    { class: "row" },
    h("span", { class: "hint" }, "Hiểu là"),
    badge({
      token: enumLabel(intent),
      gloss: "",
      state: DECLINED_INTENTS.has(intent) ? "warn" : "info",
    }),
  );
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
      intentRow(String(item.intent || "")),
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
function liveAnswer(store, clearance) {
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
   * Scroll this card into view with the composer's real height held clear beneath it.
   *
   * `scrollIntoView` reasons about the scrollport, which does not know that the composer is
   * `position: sticky; bottom` and paints over its foot. `components.css` declares a static
   * `scroll-margin-block-end` as the floor; the composer's height is not static — it grows with
   * the textarea — and at 390×844 the static value was four pixels short, which is enough to put
   * the last line of an answer behind the box the operator just typed into. Measuring at the
   * moment of the scroll is the only value that is right for the composer as it actually is.
   */
  function reveal() {
    card.style.scrollMarginBlockEnd = `${clearance()}px`;
    card.scrollIntoView({ block: "end" });
    // And again once the frame has been laid out. The first pass measures a composer that is
    // still moving: sending clears the textarea and shrinks it, the banner row above can appear
    // or go, and either shifts the composer's top after the clearance has been read from it.
    // Measured against the running stack at 390×844, that left the newest line 11px behind the
    // composer for about a second while the answer streamed — correct once it settled, wrong
    // exactly while it was being read. The second pass reads the settled layout, and is a no-op
    // when nothing moved.
    requestAnimationFrame(() => {
      card.style.scrollMarginBlockEnd = `${clearance()}px`;
      card.scrollIntoView({ block: "end" });
    });
  }

  /**
   * The finishing chrome every complete answer gets, however the text arrived.
   *
   * @param {any} turn
   */
  function finishChrome(turn) {
    render(badgeHost, intentRow(String(turn.intent || "")));
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
    // The card was scrolled clear of the composer while it was two typing dots tall. It then grows
    // downward as frames arrive, so without following it the newest words walk straight back under
    // the composer — measured at 390×844, the whole of a refusal ended up below the fold while the
    // operator watched the top of it. Following is what a person reading a chat expects.
    //
    // A wheel or a finger is an unambiguous statement that they would rather be looking somewhere
    // else, so either one ends the following for this turn and nothing puts it back. That test is
    // used instead of comparing scroll positions because the only scrolling here is ours, and a
    // position check cannot tell our scroll from theirs.
    let following = true;
    const release = () => {
      following = false;
    };
    window.addEventListener("wheel", release, { passive: true });
    window.addEventListener("touchmove", release, { passive: true });
    const stopFollowing = () => {
      window.removeEventListener("wheel", release);
      window.removeEventListener("touchmove", release);
    };
    /** Keep the newest text above the composer, measured against the composer as it now is. */
    const follow = () => {
      if (following) reveal();
    };
    source.onmessage = (event) => {
      if (event.data === "[DONE]") {
        settled = true;
        source.close();
        caret.remove();
        finishChrome(turn);
        follow();
        stopFollowing();
        return;
      }
      try {
        answerNode.appendChild(document.createTextNode(JSON.parse(event.data)));
        follow();
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
      follow();
      stopFollowing();
    };
  }

  return { card, begin, reveal };
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
    const live = liveAnswer(store, composerClearance);
    transcriptHost.append(bubble, live.card);
    // `end`, not `nearest`. `nearest` counted the new card as "in view" while the sticky composer
    // covered it, so the operator watched their answer arrive underneath the box they had just
    // typed into. `end` plus the measured clearance lands it above the composer instead.
    live.reveal();
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
   * How much room to hold clear at the foot of the scrollport for the composer.
   *
   * Read from where the composer actually is, not from what it is made of. Two things move it and
   * neither is a constant this file could hold: the textarea grows to about eight rows with what
   * the operator has typed, and below 64rem the composer floats above the bottom navigation bar by
   * that bar's own measured height. An earlier version added its height to a fixed 24px and was
   * right at one width and 81px short at the other.
   *
   * The scrollport's bottom edge is the viewport's at both breakpoints — below 64rem the document
   * scrolls, and above it `.main` scrolls but still reaches the viewport foot — so the distance
   * from the composer's top to `innerHeight` is exactly the room the composer occupies, plus one
   * `--space-3` so an answer never ends flush against it.
   *
   * @returns {number} pixels
   */
  function composerClearance() {
    const gap = 12; // var(--space-3)
    return Math.max(0, window.innerHeight - form.getBoundingClientRect().top) + gap;
  }

  /** The oldest turn on screen, and therefore where the next page backwards starts. */
  let oldestTurnId = null;

  /** Where the "older turns" control and its failures render, above the transcript. */
  const olderHost = h("div");

  /**
   * Both containers that can hold the reader's position, and by how much the content above them
   * grew. Which one actually scrolls depends on the viewport — the document below 64rem, `#main`
   * above it — so both are measured and both restored; one is a real correction and the other a
   * no-op either way. Prepending without this is the classic transcript defect: the reader is
   * looking at a sentence, older turns arrive above it, and the sentence walks off the screen.
   *
   * @returns {() => void} call after the prepend to put the reader back where they were
   */
  function holdScrollPosition() {
    const pane = document.querySelector("#main");
    const doc = document.scrollingElement || document.documentElement;
    const before = {
      paneHeight: pane ? pane.scrollHeight : 0,
      paneTop: pane ? pane.scrollTop : 0,
      docHeight: doc.scrollHeight,
      docTop: doc.scrollTop,
    };
    return () => {
      if (pane) pane.scrollTop = before.paneTop + (pane.scrollHeight - before.paneHeight);
      doc.scrollTop = before.docTop + (doc.scrollHeight - before.docHeight);
    };
  }

  /**
   * Say whether there is more history than this, and offer to fetch it.
   *
   * A page carrying exactly the limit means the server had at least that many, so the transcript
   * is truncated and the console is not allowed to end it without saying so — hidden truncation is
   * forbidden by the same rule that forbids a hidden unsupported default. Fetching more is a read,
   * so a control that repeats it is honest in a way no write control here would be.
   *
   * @param {boolean} more
   */
  function renderOlder(more) {
    if (!more) {
      render(olderHost);
      return;
    }
    render(
      olderHost,
      h(
        "div",
        { class: "chat__older" },
        h(
          "button",
          {
            type: "button",
            dataVariant: "quiet",
            dataRequiresNetwork: "true",
            onClick: (event) => void loadOlder(event.currentTarget),
          },
          "Xem thêm lượt cũ hơn",
        ),
        h(
          "p",
          { class: "hint" },
          `Đang hiện ${HISTORY_LIMIT} lượt gần nhất. Còn lượt cũ hơn chưa hiện ở đây.`,
        ),
      ),
    );
  }

  /**
   * Fetch the page before the oldest turn on screen and put it above what is already there.
   *
   * The transcript is the record of every question anyone asked the assistant, and an owner sees
   * the whole store's. Ending it at the newest page would make the older half unreachable from the
   * one surface that shows it.
   *
   * @param {HTMLElement|null} control
   */
  async function loadOlder(control) {
    if (!store || !oldestTurnId) return;
    if (control) control.disabled = true;
    const restore = holdScrollPosition();
    try {
      const body = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/assistant/turns` +
          `?limit=${HISTORY_LIMIT}&before=${encodeURIComponent(oldestTurnId)}`,
      );
      const items = Array.isArray(body) ? body : [];
      if (items.length > 0) {
        oldestTurnId = String(items[items.length - 1].turn_id || "");
        // `prepend` rather than a re-render: the turns already on screen keep their nodes, so
        // nothing the reader is looking at is rebuilt underneath them.
        transcriptHost.prepend(
          ...[...items]
            .reverse()
            .flatMap((item) => [questionBubble(item.question), answerBlock(item)]),
        );
      }
      renderOlder(isTruncated(items, HISTORY_LIMIT));
      restore();
    } catch (error) {
      if (control) control.disabled = false;
      render(olderHost, errorNotice(error, { onRetry: () => void loadOlder(null) }));
    }
  }

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
    render(olderHost);
    try {
      const body = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/assistant/turns?limit=${HISTORY_LIMIT}`,
      );
      const items = Array.isArray(body) ? body : [];
      oldestTurnId = items.length > 0 ? String(items[items.length - 1].turn_id || "") : null;
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
      renderOlder(isTruncated(items, HISTORY_LIMIT));
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
      h("div", { class: "chat__col" }, noticeHost, olderHost, transcriptHost, form),
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
