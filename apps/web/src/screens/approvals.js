/**
 * Approvals: the envelopes waiting for a human, and an honest account of what may be decided here.
 *
 * Until 2026-09-17 this screen showed a queue and then refused to let anyone work it. The refusal
 * was real rather than an oversight — `ApprovalDecisionRequest` demands `resource_version`,
 * `snapshot_hash` and `rendered_hash`, and `GET /internal/v1/approvals` returned none of the three,
 * only `envelope_hash`, which is a different value and not a substitute. No approver could build a
 * valid decision from anything any client could read, so the A2 gate had a queue and no human side:
 * an envelope expired unactioned and nobody could have prevented it.
 *
 * The three values were stored `NOT NULL` on `approval_requests` from the first migration and were
 * simply never projected. They are now, and the rules that survive are these:
 *
 *   - **Decide only what the console can show you.** Pressing "Duyệt" on a digest of content you
 *     were never shown is blind approval, and returning the hashes does not by itself fix that. So
 *     the controls enable for a resource type this console can actually render — today `ORDER`,
 *     which `#/orders/:orderId` opens — and stay disabled, with the resource type named, for every
 *     type it cannot. `MESSAGE_DRAFT` is the one that matters: nothing stores the message body, and
 *     `rendered_hash` is explicitly not verified server-side, so there is nothing to show and
 *     nothing to check it against. That is fail-closed in the same direction the server chose.
 *   - **The server is the authority, not this list.** `_require_exact_binding` re-checks the
 *     version and both digests at decision time, `_authorize_decision` re-checks store membership,
 *     role, MFA and maker-checker separation. A stale card cannot approve anything: the decision is
 *     refused, which is why a `STALE` refusal here offers a reload rather than a retry.
 *   - **Only `REQUESTED` is listed.** The `WHERE s.status = 'REQUESTED'` clause means approved,
 *     rejected and expired envelopes are not in this list and cannot be reviewed from it.
 *   - **The countdown is the point.** Approval TTLs are 10, 15 or 30 minutes by action
 *     (`SECURITY_RELIABILITY_SPEC_V1.md:354`) and an expiry never extends implicitly, so remaining
 *     time matters far more than a wall-clock timestamp. One interval drives every badge and stops
 *     itself the moment the screen leaves the document — a console is left open all day, and a
 *     leaked timer per navigation is a real bug rather than a tidiness complaint.
 *
 * The bullet this docstring used to carry first — that the queue joins through `orders` and so
 * hides every non-order resource type — was stale from migration `0034`, which gave
 * `approval_requests` its own `store_id`. The limits panel on this same screen had already been
 * corrected; the docstring had not, and the two halves of one module contradicted each other.
 *
 * @module screens/approvals
 */

import { Submission, request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { countdown, dateTime, shortHash, shortId } from "../core/format.js";
import { enumVi } from "../core/i18n.js";
import { can } from "../core/rbac.js";
import { principal } from "../core/session.js";
import {
  badge,
  errorNotice,
  explain,
  facts,
  gated,
  listView,
  panel,
  resultLine,
  setResult,
} from "../ui/components.js";

const LIST_LIMIT = 100;

/** One second. Fast enough that a badge flipping to expired is seen, slow enough to cost nothing. */
const TICK_MS = 1000;

/** The id the disabled decision controls point at, so the refusal is announced with the control. */
const BLOCK_ID = "approval-decision-blocked";

/**
 * The remaining-time badge for one envelope.
 *
 * Rebuilt whole on every tick rather than having its text patched, because the warn → danger flip
 * is carried by `data-state` and not by the words. A tick that updated only the text would leave an
 * expired envelope wearing the colour of a live one.
 *
 * @param {string|null|undefined} expiresAt
 * @returns {HTMLElement}
 */
function countdownBadge(expiresAt) {
  const left = countdown(expiresAt);
  return badge({
    token: left.text,
    gloss: left.expired
      ? "đã hết hạn — không tự gia hạn, phải tạo yêu cầu mới"
      : "thời gian còn lại trước khi hết hạn",
    state: left.expired ? "danger" : "warn",
  });
}

/**
 * Resource types whose content this console can put in front of an approver before they decide.
 *
 * The key is the server's `resource_type`; the value builds the route that shows it. A type absent
 * from this table is not decidable here, and the card says which type it is rather than a generic
 * refusal — "không xem được nội dung loại MESSAGE_DRAFT" tells an approver what to go and fix,
 * "không quyết được" does not.
 *
 * `QUOTE_REVISION` is deliberately absent even though `#/quotes` lists quotes: there is no GET for
 * a single revision and no endpoint returns `quote_lines` (`gaps.js`), so the approver would see a
 * total and a hash, never the lines being priced. That is the same blind approval in a politer
 * font.
 *
 * @type {Record<string, (resourceId: string) => string>}
 */
const VIEWABLE_RESOURCES = {
  ORDER: (resourceId) => `#/orders/${encodeURIComponent(resourceId)}`,
};

/** What this console records as its reason; the server only constrains the shape. */
const DECISION_REASONS = {
  APPROVED: "APPROVED_AFTER_CONSOLE_REVIEW",
  REJECTED: "REJECTED_AFTER_CONSOLE_REVIEW",
};

/**
 * Whether one queue row carries everything a valid decision needs.
 *
 * All three are required by `ApprovalDecisionRequest`, and a row missing any of them is a row this
 * client must not try to decide from — sending a partial binding would be refused server-side
 * anyway, and the refusal would read as a bug rather than as a missing field.
 *
 * @param {any} item
 * @returns {boolean}
 */
function hasBinding(item) {
  return (
    Number.isInteger(item.resource_version) &&
    typeof item.snapshot_hash === "string" &&
    typeof item.rendered_hash === "string"
  );
}

/**
 * The two controls an approver uses, enabled only when this console can show the thing being
 * approved.
 *
 * Still not wrapped in `gated()` for the disabled case: `gated()` says "your role may not", and a
 * type this screen cannot render is refused for `OWNER_ADMIN` too. The role verdict is applied on
 * top, for the enabled case only, so an auditor sees the role reason and an owner looking at a
 * `MESSAGE_DRAFT` sees the content reason.
 *
 * @param {any} item
 * @param {() => Promise<void>} onDecided
 * @param {{allowed: boolean, reason: string}} verdict
 * @returns {HTMLElement}
 */
function decisionControls(item, onDecided, verdict) {
  const viewer = VIEWABLE_RESOURCES[String(item.resource_type)];
  const decidable = Boolean(viewer) && hasBinding(item);
  const host = resultLine();

  if (!decidable) {
    const control = (label) =>
      h(
        "button",
        { type: "button", disabled: true, "aria-disabled": "true", "aria-describedby": BLOCK_ID },
        label,
      );
    return h(
      "div",
      { class: "stack stack--tight" },
      h("div", { class: "form__actions" }, control("Duyệt"), control("Từ chối")),
      h(
        "p",
        { class: "hint" },
        !viewer
          ? `Không bấm được: bảng vận hành chưa mở được nội dung loại ${item.resource_type} để ` +
            "bạn xem trước khi quyết. Duyệt một nội dung chưa xem là duyệt mù. Xem “Tại sao nút " +
            "Duyệt đang tắt?” bên dưới."
          : "Không bấm được: phiếu này thiếu phiên bản hoặc mã niêm phong, nên không dựng được " +
            "một quyết định hợp lệ. Tải lại hàng chờ.",
      ),
      host,
    );
  }

  const submission = new Submission(`approval-decision-${item.approval_request_id}`);

  /** @param {"APPROVED"|"REJECTED"} decision */
  const send = async (decision, button, sibling) => {
    button.disabled = true;
    sibling.disabled = true;
    setResult(host, "warn", decision === "APPROVED" ? "Đang ghi phê duyệt…" : "Đang ghi từ chối…");
    try {
      await request(
        `/internal/v1/approvals/${encodeURIComponent(item.approval_request_id)}/decisions`,
        {
          method: "POST",
          body: {
            decision,
            reason_code: DECISION_REASONS[decision],
            // Sent back exactly as the queue gave them. The server re-checks all three against the
            // stored row, so a card that went stale while the approver was reading is refused
            // rather than silently deciding about an older version.
            resource_version: item.resource_version,
            snapshot_hash: item.snapshot_hash,
            rendered_hash: item.rendered_hash,
          },
          idempotencyKey: submission.key(),
        },
      );
      submission.reset();
      // Reported to the screen, not to this card. `onDecided()` reloads the queue and a decided
      // envelope is no longer `REQUESTED`, so the card this line lives in is removed a moment
      // later -- the operator would watch the row vanish with no statement that their decision
      // was recorded, which is the one thing they need to know.
      setResult(host, "ok", decision === "APPROVED" ? "Đã phê duyệt." : "Đã từ chối.");
      await onDecided(
        decision === "APPROVED"
          ? `Đã phê duyệt phiếu ${shortId(item.approval_request_id)}. Phiếu rời khỏi hàng chờ.`
          : `Đã từ chối phiếu ${shortId(item.approval_request_id)}. Phiếu rời khỏi hàng chờ.`,
      );
    } catch (error) {
      button.disabled = false;
      sibling.disabled = false;
      const stale = error.kind === "STALE" || error.kind === "PRECONDITION_REQUIRED";
      setResult(
        host,
        error.kind === "REQUIRE_HUMAN" ? "warn" : "danger",
        stale
          ? "Phiếu này vừa đổi trong lúc bạn đang xem, nên quyết định của bạn bị từ chối và " +
            "không có gì được ghi. Tải lại hàng chờ rồi đọc lại phiếu mới."
          : "Không ghi được quyết định. Máy chủ nêu lý do bên dưới, nguyên văn. Không có gì " +
            "được ghi.",
      );
      render(host.parentElement || host, errorNotice(error));
    }
  };

  const approve = h("button", { type: "button", dataRequiresNetwork: "true" }, "Duyệt");
  const reject = h("button", { type: "button", dataRequiresNetwork: "true" }, "Từ chối");
  approve.addEventListener("click", () => void send("APPROVED", approve, reject));
  reject.addEventListener("click", () => void send("REJECTED", reject, approve));

  return h(
    "div",
    { class: "stack stack--tight" },
    h(
      "p",
      { class: "hint" },
      h("a", { href: viewer(String(item.resource_id)) }, "Mở nội dung này trước khi quyết"),
      " — máy chủ kiểm lại phiên bản và cả hai mã niêm phong khi bạn bấm.",
    ),
    h("div", { class: "form__actions" }, gated(approve, verdict), gated(reject, verdict)),
    h(
      "p",
      { class: "hint" },
      "Máy chủ từ chối quyết định của chính người đã tạo yêu cầu, nên phiếu do bạn mở sẽ bị từ " +
        "chối ở bước này.",
    ),
    host,
  );
}

/**
 * One pending envelope.
 *
 * @param {any} item
 * @param {(host: HTMLElement, expiresAt: string) => void} registerClock
 * @param {() => Promise<void>} onDecided
 * @param {{allowed: boolean, reason: string}} verdict
 * @returns {HTMLElement}
 */
function approvalCard(item, registerClock, onDecided, verdict) {
  const clockHost = h("span", { class: "row" }, countdownBadge(item.expires_at));
  registerClock(clockHost, item.expires_at);

  return h(
    "article",
    { class: "card stack" },
    h(
      "div",
      { class: "spread" },
      h(
        "strong",
        { class: "mono", title: item.approval_request_id },
        shortId(item.approval_request_id),
      ),
      clockHost,
    ),
    facts([
      ["Trạng thái", enumVi(item.status)],
      ["Ai được quyết", enumVi(item.required_role), { span: true }],
      // What is actually being approved. Absent until the decision binding was projected, which is
      // why the queue read as a list of opaque envelope hashes rather than a list of decisions.
      ["Loại nội dung", item.resource_type || "—", { mono: true }],
      [
        "Phiên bản",
        item.resource_version == null ? "—" : `v${item.resource_version}`,
        { mono: true },
      ],
      ["Hết hạn lúc", dateTime(item.expires_at)],
      [
        "Mã niêm phong",
        h("span", { title: item.envelope_hash || "" }, shortHash(item.envelope_hash)),
        { mono: true, span: true },
      ],
    ]),
    item.replayed
      ? h(
          "div",
          { class: "notice", dataState: "info" },
          "Lệnh này đã chạy trước đó — đây là bản ghi cũ hiện lại.",
        )
      : null,
    decisionControls(item, onDecided, verdict),
  );
}

/**
 * Everything this screen deliberately does not offer, folded behind the questions it answers.
 *
 * The queue renders above this panel — the day's most time-critical list is the data, and the
 * education about the data comes after it (UX refactor spec WS2). Nothing here is deleted: each
 * standing notice keeps its exact text, moved into an `explain()` whose summary names what it
 * explains. The read-only guardrail stays visible because it states what the whole screen cannot
 * do, and the refusal reason for the disabled decision buttons stays visible under them.
 *
 * @returns {HTMLElement}
 */
function limitsPanel() {
  return panel({
    eyebrow: "Giới hạn",
    title: "Giới hạn của màn hình này",
    guardrail:
      "Quyết định ở đây ghi thẳng vào máy chủ và không hoàn tác được. Trước khi ghi, máy chủ " +
      "kiểm lại phiên bản, cả hai mã niêm phong, quyền của bạn, và quy tắc người tạo yêu cầu " +
      "không được tự duyệt.",
    children: h(
      "div",
      { class: "stack" },
      explain(
        "Danh sách này có phải toàn bộ hàng chờ không?",
        h(
          "div",
          { class: "notice", dataState: "warn" },
          h("p", { class: "notice__title" }, "Hiện mọi phê duyệt của cửa hàng bạn thuộc về"),
          h(
            "p",
            null,
            "Máy chủ tìm hàng chờ theo cửa hàng ghi trên chính phiếu duyệt, nên mọi loại đều hiện " +
              "ở đây: đơn hàng, báo giá, tin nhắn soạn sẵn, khung giờ và phí giao đề xuất. Trước " +
              "ngày 31/08/2026 máy chủ tìm theo đơn hàng, nên toàn bộ hàng chờ gửi tin nhắn — " +
              "thứ mà chính những người phải xử lý nó cần thấy — không bao giờ hiện ra.",
          ),
          h(
            "p",
            { class: "hint" },
            "Chỉ hiện phê duyệt của cửa hàng bạn được phân công. Phiếu của cửa hàng khác không " +
              "hiện ở đây và cũng không duyệt được.",
          ),
        ),
        h(
          "div",
          { class: "notice", dataState: "info" },
          h("p", { class: "notice__title" }, "Chỉ hiện việc đang chờ quyết"),
          h(
            "p",
            null,
            "Việc đã duyệt, đã từ chối hoặc đã hết hạn không nằm trong danh sách này và không tra " +
              "cứu lại được từ đây.",
          ),
        ),
      ),
      explain(
        "Tại sao nút Duyệt đang tắt?",
        h(
          "div",
          { class: "notice", dataState: "warn", id: BLOCK_ID },
          h("p", { class: "notice__title" }, "Khoá theo loại nội dung, không phải theo vai trò"),
          h(
            "p",
            null,
            "Máy chủ đòi phiên bản và hai mã niêm phong để chứng minh bạn quyết đúng nội dung đó. " +
              "Hàng chờ nay trả về đủ cả ba, nên phiếu nào bảng vận hành mở ra xem được thì bấm " +
              "quyết được ngay — hôm nay là phiếu gắn với một đơn hàng.",
          ),
          h(
            "p",
            null,
            "Loại nào bảng vận hành chưa mở ra xem được thì nút vẫn khoá, và đó là cố ý. " +
              "MESSAGE_DRAFT là loại đáng nói nhất: hệ thống không lưu nội dung tin nhắn, và máy " +
              "chủ cũng không đối chiếu được mã niêm phong nội dung với bất cứ thứ gì. Bấm duyệt " +
              "một tin sắp gửi cho khách mà chưa ai đọc được nó chính là duyệt mù — có đủ ba mã " +
              "cũng không làm điều đó thành an toàn.",
          ),
          h(
            "p",
            null,
            h("a", { href: "#/gaps" }, "Xem danh mục việc chưa hỗ trợ"),
          ),
        ),
      ),
      explain(
        "Vì sao không tạo được yêu cầu duyệt ở đây?",
        h(
          "div",
          { class: "notice", dataState: "info" },
          h("p", { class: "notice__title" }, "Tạo yêu cầu duyệt cũng không có ở đây"),
          h(
            "p",
            null,
            "Cùng một lý do: mở một yêu cầu duyệt cần đúng loại việc theo bảng của máy chủ, cộng " +
              "hai mã niêm phong và phiên bản chính sách đang áp dụng. Không giá trị nào trong số " +
              "đó nhân viên gõ tay ra được, nên màn hình không mời bạn thử.",
          ),
        ),
      ),
      explain(
        "Phê duyệt còn những quy tắc nào khác?",
        h(
          "dl",
          { class: "fields" },
          h(
            "div",
            { class: "field field--span" },
            h("dt", null, "Tách vai người làm / người duyệt"),
            h(
              "dd",
              null,
              "Máy chủ từ chối quyết định đến từ chính nhân viên đã tạo yêu cầu. Danh sách này không " +
                "trả về ai là người yêu cầu, nên bạn chỉ biết mình vướng quy tắc đó khi máy chủ từ chối.",
            ),
          ),
          h(
            "div",
            { class: "field field--span" },
            h("dt", null, "Thời hạn quyết định"),
            h(
              "dd",
              null,
              "10, 15 hoặc 30 phút tuỳ hành động, và hết hạn thì không bao giờ được gia hạn ngầm. " +
                "Hết giờ nghĩa là phải tạo lại yêu cầu mới, không phải xin thêm thời gian.",
            ),
          ),
          h(
            "div",
            { class: "field field--span" },
            h("dt", null, "Phạm vi cửa hàng"),
            h(
              "dd",
              null,
              "Hàng chờ này gồm mọi cửa hàng mà tài khoản của bạn được gán, không lọc theo cửa hàng " +
                "đang chọn ở thanh phía trên.",
            ),
          ),
        ),
      ),
    ),
  });
}

/**
 * @returns {HTMLElement}
 */
export function render_() {
  /**
   * The live countdown badges, rebuilt every time the list reloads.
   *
   * Held as a list rather than read out of the DOM so that one interval — started once, in this
   * function — drives every badge no matter how many times the list is refreshed. The registry is
   * cleared around each reload (the queue's hooks below) and refilled as the new cards are built.
   *
   * @type {Array<{host: HTMLElement, expiresAt: string}>}
   */
  const clocks = [];

  // Read once per screen build rather than per card: the principal cannot change under a rendered
  // screen without the router replacing it, and `can()` is not free enough to run per row.
  const decideVerdict = can(principal(), "APPROVALS_DECIDE");

  // Lives above the queue, so a decision's confirmation outlives the card that carried the button.
  const decisionStatus = resultLine();

  /**
   * The queue. One `listView` owns the fetch–truncate–skeleton cycle; what stays here is the clock
   * registry above. Server order is `ORDER BY expires_at, id` — the envelope dying soonest is
   * first — and it is not re-sorted here: the order is itself information about what to look at.
   */
  const queue = listView({
    limit: LIST_LIMIT,
    fetch: () => request(`/internal/v1/approvals?limit=${LIST_LIMIT}`),
    renderItem: (item) =>
      approvalCard(
        item,
        (host, expiresAt) => clocks.push({ host, expiresAt }),
        async (message) => {
          setResult(decisionStatus, "ok", message);
          await queue.reload();
        },
        decideVerdict,
      ),
    // "gắn với đơn hàng" was true before migration 0034, when the queue joined through `orders` and
    // could only ever show order-linked approvals. Since 0034 the server joins on the approval's
    // own `store_id`, so every resource type in the caller's stores appears -- and the limits panel
    // on this same screen already said so, which made the two halves contradict each other.
    emptyText:
      "Không có việc nào đang chờ bạn quyết định trong các cửa hàng bạn được gán. Đây không " +
      "phải bằng chứng là hàng chờ trống — xem bảng giới hạn bên dưới.",
    clearMetaOnError: true,
    onLoadStart: () => {
      clocks.length = 0;
    },
    onLoaded: () => {
      clocks.length = 0;
    },
    onError: () => {
      clocks.length = 0;
    },
  });

  const root = h(
    "section",
    { class: "screen" },
    h(
      "div",
      { class: "screen__header" },
      h("p", { class: "eyebrow" }, "Cần bạn quyết"),
      h("h1", null, "Duyệt"),
      h(
        "p",
        { class: "screen__lede" },
        "Việc đang chờ duyệt và còn bao lâu nữa hết hạn. Quyết được ngay tại đây với loại nội " +
          "dung bảng vận hành mở ra xem được; loại nào chưa xem được thì nút vẫn khoá và phiếu " +
          "nói rõ đó là loại nào.",
      ),
    ),
    panel({
      eyebrow: "Đang chờ",
      title: "Việc chờ bạn quyết",
      count: queue.count,
      children: h(
        "div",
        { class: "stack" },
        queue.bar.node,
        decisionStatus,
        queue.truncation,
        queue.host,
      ),
    }),
    limitsPanel(),
  );

  // One interval for the whole screen. It checks that the screen is still in the document before
  // doing any work and clears itself when it is not, so navigating away — or the router replacing
  // this screen on a session change — cannot leave a timer running against a detached tree.
  const timer = setInterval(() => {
    if (!root.isConnected) {
      clearInterval(timer);
      return;
    }
    for (const clock of clocks) render(clock.host, countdownBadge(clock.expiresAt));
  }, TICK_MS);

  void queue.reload();

  return root;
}

export const screen = {
  path: "/approvals",
  title: "Duyệt",
  capability: "APPROVALS_READ",
  render: render_,
};
