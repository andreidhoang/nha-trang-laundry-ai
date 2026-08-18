/**
 * Approvals: the envelopes waiting for a human, and an honest account of what this screen cannot do.
 *
 * This is the only screen in the console that shows a queue and then refuses to let the operator
 * work it. That is deliberate, and the reasons are load-bearing rather than incidental:
 *
 *   - **The queue is a filtered view, and the filter is invisible server-side.** `list_pending`
 *     resolves each request through `approval_requests → orders → staff_store_assignments`, so an
 *     approval whose resource is a `QUOTE_REVISION`, a `MESSAGE_DRAFT`, a `SLOT_PROPOSAL` or a
 *     `DELIVERY_FEE_PROPOSAL` never appears — its `resource_id` is not an order id and the join
 *     drops it. An empty list here does not mean nothing is pending, so the screen says so in copy
 *     rather than letting the absence speak.
 *   - **Only `REQUESTED` is listed.** The `WHERE s.status = 'REQUESTED'` clause means approved,
 *     rejected and expired envelopes are not in this list and cannot be reviewed from it.
 *   - **Deciding is not offered, and not because a button was forgotten.** A decision requires
 *     `resource_version`, `snapshot_hash` and `rendered_hash`. The queue response carries none of
 *     the three — only `envelope_hash`, which is a *different* value and not a substitute. An
 *     approver therefore cannot construct a valid decision from anything this screen can see, and a
 *     form asking someone to paste three hashes for content they have not been shown is blind
 *     approval of a message that goes to a customer. So the controls are present, disabled, and
 *     explained. There is no paste field, on purpose.
 *   - **The countdown is the point.** Approval TTLs are 10, 15 or 30 minutes by action
 *     (`SECURITY_RELIABILITY_SPEC_V1.md:354`) and an expiry never extends implicitly, so remaining
 *     time matters far more than a wall-clock timestamp. One interval drives every badge and stops
 *     itself the moment the screen leaves the document — a console is left open all day, and a
 *     leaked timer per navigation is a real bug rather than a tidiness complaint.
 *
 * Nothing here mutates. There is no `Submission` and no idempotency key in this module because
 * there is no write to carry one.
 *
 * @module screens/approvals
 */

import { request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { countdown, dateTime, shortHash, shortId } from "../core/format.js";
import { enumVi } from "../core/i18n.js";
import { badge, explain, facts, listView, panel } from "../ui/components.js";

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
      ? "phong bì đã hết hạn — không có gia hạn ngầm, phải tạo yêu cầu mới"
      : "thời gian còn lại trước khi phong bì hết hạn",
    state: left.expired ? "danger" : "warn",
  });
}

/**
 * The two controls an approver would use, if the API let them.
 *
 * Not wrapped in `gated()`. `gated()` says "your role may not"; this is not a role problem — an
 * `OWNER_ADMIN` with MFA is refused by the same missing fields, so a role-conditional control would
 * enable itself for exactly the person most able to do harm with it.
 *
 * @returns {HTMLElement}
 */
function decisionControls() {
  /** @param {string} label */
  const control = (label) =>
    h(
      "button",
      {
        type: "button",
        disabled: true,
        "aria-disabled": "true",
        "aria-describedby": BLOCK_ID,
      },
      label,
    );

  return h(
    "div",
    { class: "stack stack--tight" },
    h("div", { class: "form__actions" }, control("Duyệt"), control("Từ chối")),
    h(
      "p",
      { class: "hint" },
      "Không bấm được: quyết định cần ba giá trị mà danh sách này không trả về. Xem “Tại sao nút " +
        "Duyệt đang tắt?” trong bảng giới hạn bên dưới.",
    ),
  );
}

/**
 * One pending envelope.
 *
 * @param {any} item
 * @param {(host: HTMLElement, expiresAt: string) => void} registerClock
 * @returns {HTMLElement}
 */
function approvalCard(item, registerClock) {
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
    decisionControls(),
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
      "Đây là màn hình chỉ đọc. Không có thao tác nào ở đây ghi vào máy chủ, kể cả khi phong bì " +
      "sắp hết hạn.",
    children: h(
      "div",
      { class: "stack" },
      explain(
        "Danh sách này có phải toàn bộ hàng chờ không?",
        h(
          "div",
          { class: "notice", dataState: "warn" },
          h("p", { class: "notice__title" }, "Chỉ hiện phê duyệt gắn với đơn hàng"),
          h(
            "p",
            null,
            "Máy chủ tìm hàng chờ theo đơn hàng, nên việc chờ duyệt nào không gắn với một đơn — " +
              "một bản báo giá, một tin nhắn soạn sẵn, một khung giờ hay một mức phí giao đề " +
              "xuất — sẽ không hiện ở đây. Chúng vẫn đang chờ và vẫn đang đếm ngược; chỉ là danh " +
              "sách này không thấy chúng.",
          ),
          h(
            "p",
            { class: "hint" },
            "Hệ quả: danh sách trống không có nghĩa là không còn gì chờ duyệt.",
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
          h("p", { class: "notice__title" }, "Vì sao không quyết định được ở đây"),
          h(
            "p",
            null,
            "Để duyệt, máy chủ đòi ba mã niêm phong chứng minh bạn đã xem đúng nội dung đó. " +
              "Danh sách trên không trả về mã nào trong ba mã ấy — nó chỉ có mã của chính phong " +
              "bì, là một giá trị khác và không thay được. Nên từ những gì màn hình này thấy, " +
              "không dựng nổi một quyết định hợp lệ.",
          ),
          h(
            "p",
            null,
            "Gõ tay mấy mã đó để duyệt một nội dung bạn chưa được xem chính là duyệt mù một tin " +
              "nhắn sắp gửi tới khách. Nên màn hình này cố ý không có ô để dán, và hai nút " +
              "Duyệt / Từ chối để hiện nhưng khoá — không phải quên làm.",
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
            h("dt", null, "Thời hạn phong bì"),
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

  /**
   * The queue. One `listView` owns the fetch–truncate–skeleton cycle; what stays here is the clock
   * registry above. Server order is `ORDER BY expires_at, id` — the envelope dying soonest is
   * first — and it is not re-sorted here: the order is itself information about what to look at.
   */
  const queue = listView({
    limit: LIST_LIMIT,
    fetch: () => request(`/internal/v1/approvals?limit=${LIST_LIMIT}`),
    renderItem: (item) =>
      approvalCard(item, (host, expiresAt) => clocks.push({ host, expiresAt })),
    emptyText:
      "Không có phong bì nào gắn với đơn hàng đang chờ bạn quyết định. Đây không phải bằng " +
      "chứng là hàng chờ trống — xem bảng giới hạn bên dưới.",
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
      h("p", { class: "eyebrow" }, "Chỉ đọc"),
      h("h1", null, "Duyệt"),
      h(
        "p",
        { class: "screen__lede" },
        "Việc đang chờ duyệt và còn bao lâu nữa hết hạn. Chưa quyết được từ màn hình này; " +
          "phần bên dưới nói rõ vì sao.",
      ),
    ),
    panel({
      eyebrow: "Đang chờ",
      title: "Việc chờ bạn quyết",
      count: queue.count,
      children: h("div", { class: "stack" }, queue.bar.node, queue.truncation, queue.host),
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
